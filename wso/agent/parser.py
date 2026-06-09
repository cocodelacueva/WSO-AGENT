"""Parser XML streaming tolerante para la salida del modelo.

Consume chunks de texto a medida que llegan del modelo y emite eventos
estructurados para que el loop reaccione en tiempo real.

Eventos emitidos
----------------
    - ThinkingChunk(text)        : texto del thinking, en streaming
    - ThinkingEnd()              : se cerró un bloque <thinking>
    - ToolCallComplete(name,args): tool detectada y parseada (lista para ejecutar)
    - PlainTextChunk(text)       : texto fuera de tags (anomalía — el modelo
                                   debería usar responder_al_usuario)
    - ParseError(message)        : recovery de un estado mal cerrado

Tolerancia
----------
    - whitespace flexible dentro de tags (`< thinking >` y `<thinking>`)
    - case insensitive para nombres de tags
    - múltiples bloques <thinking> antes de un <tool> (el modelo puede
      intercalar pensamiento)
    - tags mal cerrados al final del stream → ParseError en finalize()

Limitaciones (v1)
-----------------
    - el body de <tool> NO soporta XML anidado: el regex de child args
      asume que los valores son texto plano. Si el modelo necesita
      pasar XML en un arg (raro), usar escape o CDATA en v2.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto

# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------


@dataclass
class ThinkingChunk:
    """Chunk de texto dentro de un bloque <thinking>."""

    text: str


@dataclass
class ThinkingEnd:
    """Marca el cierre de un bloque <thinking>."""


@dataclass
class ToolCallComplete:
    """Tool call totalmente parseada, lista para ejecutar."""

    name: str
    args: dict[str, str]


@dataclass
class PlainTextChunk:
    """Texto que apareció fuera de cualquier tag.

    El modelo debería emitir respuestas al usuario vía
    responder_al_usuario, no como texto plano. Esto es informativo —
    el loop puede ignorarlo o loguearlo.
    """

    text: str


@dataclass
class ParseError:
    """Recovery de un estado inválido (típicamente en finalize)."""

    message: str


ParseEvent = ThinkingChunk | ThinkingEnd | ToolCallComplete | PlainTextChunk | ParseError


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


class ParseState(Enum):
    OUTSIDE = auto()
    IN_THINKING = auto()
    IN_TOOL = auto()


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


# Aperturas: tolerantes a whitespace y case
_THINKING_OPEN_RE = re.compile(r"<\s*thinking\s*>", re.IGNORECASE)
_TOOL_OPEN_RE = re.compile(
    r"""<\s*tool\s+name\s*=\s*["']([^"']+)["']\s*(/?)>""",
    re.IGNORECASE,
)

# Cierres: tolerantes a whitespace y case
_THINKING_CLOSE_RE = re.compile(r"<\s*/\s*thinking\s*>", re.IGNORECASE)
_TOOL_CLOSE_RE = re.compile(r"<\s*/\s*tool\s*>", re.IGNORECASE)

# Child arg dentro del body de un tool: <name>value</name>
# Note: case-sensitive para arg names (deben matchear la signature exacta).
_ARG_RE = re.compile(r"<\s*(\w+)\s*>(.*?)<\s*/\s*\1\s*>", re.DOTALL)

# Tolerancia: arg abierto SIN cerrar al final del body. Los modelos chicos a
# veces emiten `<slides_json>[...]` y saltan directo a `</tool>` olvidando el
# `</slides_json>`. Capturamos ese último arg hasta el final del body para no
# perder el contenido (y evitar loops de "Field required"). Ver DESIGN 3.17/3.18.
_UNCLOSED_ARG_RE = re.compile(r"<\s*(\w+)\s*>(.*)\Z", re.DOTALL)

# CDATA wrap: algunos modelos envuelven el contenido en <![CDATA[...]]>
# pensando que escapan caracteres XML. Lo strippeamos para no contaminar
# HTML/CSS/JS escritos vía write_file.
_CDATA_RE = re.compile(r"^<!\[CDATA\[(.*)\]\]>$", re.DOTALL)


# Para el hold-back de partials, usamos los strings canónicos como
# referencia de "longest possible suffix that could become this tag".
_THINKING_CLOSE_CANONICAL = "</thinking>"
_TOOL_CLOSE_CANONICAL = "</tool>"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class StreamingXMLParser:
    """Parser de estado para la salida streaming del modelo.

    Uso típico (en el loop):

        parser = StreamingXMLParser()
        async for chunk in model.stream_chat(messages):
            for event in parser.feed(chunk):
                handle(event)
        for event in parser.finalize():
            handle(event)
    """

    def __init__(self) -> None:
        self.state: ParseState = ParseState.OUTSIDE
        self._buffer: str = ""
        self._tool_name: str | None = None
        self._tool_body: str = ""

    # ---- API pública ----

    def feed(self, chunk: str) -> Iterator[ParseEvent]:
        """Consumir un chunk de tokens y emitir eventos detectados."""
        self._buffer += chunk
        yield from self._process()

    def finalize(self) -> Iterator[ParseEvent]:
        """Cerrar la sesión de parsing — flush de cualquier estado abierto."""
        if self.state == ParseState.OUTSIDE:
            if self._buffer.strip():
                yield PlainTextChunk(self._buffer)
        elif self.state == ParseState.IN_THINKING:
            if self._buffer:
                yield ThinkingChunk(self._buffer)
            yield ThinkingEnd()
        elif self.state == ParseState.IN_TOOL:
            yield ParseError(
                f"Tool {self._tool_name!r} no se cerró correctamente "
                f"(falta </tool>)."
            )
        self._reset()

    # ---- Loop de procesamiento ----

    def _process(self) -> Iterator[ParseEvent]:
        """Iterar consumiendo el buffer hasta no poder progresar más."""
        while self._buffer:
            if self.state == ParseState.OUTSIDE:
                made_progress = yield from self._process_outside()
            elif self.state == ParseState.IN_THINKING:
                made_progress = yield from self._process_thinking()
            elif self.state == ParseState.IN_TOOL:
                made_progress = yield from self._process_tool()
            else:  # pragma: no cover
                made_progress = False

            if not made_progress:
                break

    # ---- Estado OUTSIDE ----

    def _process_outside(self) -> Iterator[ParseEvent]:
        """Buscar próximo opening de <thinking> o <tool ...>."""
        thinking_match = _THINKING_OPEN_RE.search(self._buffer)
        tool_match = _TOOL_OPEN_RE.search(self._buffer)

        # Elegir el match más temprano
        match: re.Match[str] | None = None
        kind: str = ""
        if thinking_match and tool_match:
            if thinking_match.start() < tool_match.start():
                match, kind = thinking_match, "thinking"
            else:
                match, kind = tool_match, "tool"
        elif thinking_match:
            match, kind = thinking_match, "thinking"
        elif tool_match:
            match, kind = tool_match, "tool"

        if match is not None:
            # Emitir cualquier texto plano que precede al tag
            prefix = self._buffer[: match.start()]
            if prefix.strip():
                yield PlainTextChunk(prefix)
            self._buffer = self._buffer[match.end():]

            if kind == "thinking":
                self.state = ParseState.IN_THINKING
            else:
                tool_name = match.group(1)
                is_self_closing = match.group(2) == "/"
                if is_self_closing:
                    yield ToolCallComplete(name=tool_name, args={})
                else:
                    self._tool_name = tool_name
                    self._tool_body = ""
                    self.state = ParseState.IN_TOOL
            return True

        # No hay tag completo — chequear partial al final del buffer
        hold_pos = self._hold_position_outside()
        if hold_pos > 0:
            prefix = self._buffer[:hold_pos]
            if prefix.strip():
                yield PlainTextChunk(prefix)
            self._buffer = self._buffer[hold_pos:]
        return False

    # ---- Estado IN_THINKING ----

    def _process_thinking(self) -> Iterator[ParseEvent]:
        close_match = _THINKING_CLOSE_RE.search(self._buffer)
        if close_match is not None:
            content = self._buffer[: close_match.start()]
            if content:
                yield ThinkingChunk(content)
            yield ThinkingEnd()
            self._buffer = self._buffer[close_match.end():]
            self.state = ParseState.OUTSIDE
            return True

        # Stream lo que se pueda, retener el sufijo ambiguo
        hold_pos = self._suffix_hold_position(_THINKING_CLOSE_CANONICAL)
        if hold_pos > 0:
            yield ThinkingChunk(self._buffer[:hold_pos])
            self._buffer = self._buffer[hold_pos:]
        return False

    # ---- Estado IN_TOOL ----

    def _process_tool(self) -> Iterator[ParseEvent]:
        close_match = _TOOL_CLOSE_RE.search(self._buffer)
        if close_match is not None:
            self._tool_body += self._buffer[: close_match.start()]
            args = self._parse_tool_body(self._tool_body)
            yield ToolCallComplete(name=self._tool_name or "", args=args)
            self._buffer = self._buffer[close_match.end():]
            self._tool_name = None
            self._tool_body = ""
            self.state = ParseState.OUTSIDE
            return True

        # Acumular body hasta el sufijo ambiguo
        hold_pos = self._suffix_hold_position(_TOOL_CLOSE_CANONICAL)
        if hold_pos > 0:
            self._tool_body += self._buffer[:hold_pos]
            self._buffer = self._buffer[hold_pos:]
        return False

    # ---- Helpers de hold-back ----

    def _hold_position_outside(self) -> int:
        """Determinar dónde empezar a retener cuando estamos OUTSIDE.

        Si el buffer termina con un `<` sin cerrar (`>` no aparece después),
        ese `<` podría ser el inicio de `<thinking>` o `<tool ...>`.
        Mantenemos desde ahí.
        """
        last_lt = self._buffer.rfind("<")
        if last_lt < 0:
            return len(self._buffer)
        if ">" in self._buffer[last_lt:]:
            # El `<` ya tiene su cierre y la regex no matcheó —
            # es texto plano, emitir todo.
            return len(self._buffer)
        return last_lt

    def _suffix_hold_position(self, target: str) -> int:
        """Encontrar el sufijo más largo del buffer que sea prefijo del target.

        Usado dentro de IN_THINKING / IN_TOOL para retener cualquier tail
        que podría convertirse en `</thinking>` o `</tool>` con más input.
        """
        max_n = min(len(self._buffer), len(target) - 1)
        for n in range(max_n, 0, -1):
            if target.startswith(self._buffer[-n:]):
                return len(self._buffer) - n
        return len(self._buffer)

    # ---- Parsing del tool body ----

    def _parse_tool_body(self, body: str) -> dict[str, str]:
        """Extraer pares <arg>value</arg> del body de un <tool>.

        Tolera un último arg abierto sin cerrar (el modelo olvidó `</arg>`
        antes de `</tool>`): su contenido se captura hasta el final del body.

        Note:
            No soporta XML anidado en valores (limitación conocida v1).
        """
        args: dict[str, str] = {}
        last_end = 0
        for match in _ARG_RE.finditer(body):
            name = match.group(1)
            value = _strip_cdata(match.group(2).strip())
            args[name] = value
            last_end = match.end()

        # Recovery del último arg sin cerrar: buscamos un `<arg>` abierto en lo
        # que quedó del body (después del último arg bien cerrado) sin su
        # `</arg>` correspondiente, y tomamos su contenido hasta el final.
        tail = body[last_end:]
        unclosed = _UNCLOSED_ARG_RE.search(tail)
        if unclosed:
            name = unclosed.group(1)
            value = _strip_cdata(unclosed.group(2).strip())
            if name not in args and value:
                args[name] = value
        return args

    # ---- Reset ----

    def _reset(self) -> None:
        self._buffer = ""
        self._tool_name = None
        self._tool_body = ""
        self.state = ParseState.OUTSIDE


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


def _strip_cdata(value: str) -> str:
    """Si el valor está envuelto en <![CDATA[...]]>, devolver solo el contenido.

    Algunos modelos (sobre todo locales y Gemini) envuelven los args en
    CDATA pensando que escapan caracteres XML. Como nuestro parser no
    es un XML parser real, los marcadores CDATA terminan en el archivo
    de destino y rompen HTML/CSS/JS. Esta función limpia el wrap.
    """
    match = _CDATA_RE.match(value)
    if match:
        return match.group(1).strip()
    return value
