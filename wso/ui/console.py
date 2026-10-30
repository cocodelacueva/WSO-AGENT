"""Renderer centralizado para la salida del agente.

Toda la presentación visual del agente pasa por este módulo. Si querés
cambiar el estilo (colores, indentación, símbolos), lo hacés acá sin
tocar lógica del loop.

Convenciones de estilo:
    respuesta del modelo → texto normal, streaming
    thinking             → dim italic (gris), streaming
    tool call            → cyan, prefijo "▶"
    resultado de tool    → dim, prefijo "←", recortado
    error                → rojo, prefijo "✗"
    info del sistema     → dim
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.console import Console
from rich.text import Text

# ---------------------------------------------------------------------------
# Constantes de estilo
# ---------------------------------------------------------------------------

_STYLE_TEXT = ""
_STYLE_THINKING = "dim italic"
_STYLE_TOOL_NAME = "bold cyan"
_STYLE_TOOL_ARGS = "cyan"
_STYLE_OBSERVATION = "dim"
_STYLE_ERROR = "red"

_ARG_MAX_LENGTH = 80
_OBSERVATION_MAX_CHARS = 400
_OBSERVATION_MAX_LINES = 6

# Arg principal de cada tool: se muestra solo, sin "nombre=".
_MAIN_ARG = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "glob": "pattern",
    "grep": "pattern",
    "bash": "command",
}
# Args que no aportan en la línea de la tool call (son largos).
_HIDDEN_ARGS = {"content", "old_string", "new_string"}


@dataclass
class ConsoleRenderer:
    """Wrapper del Console de Rich con métodos semánticos.

    Lleva el estado del streaming (si estamos escribiendo texto o thinking)
    para insertar los saltos de línea correctos entre uno y otro.
    """

    console: Console
    _stream_mode: Literal["text", "thinking"] | None = field(default=None, init=False)

    # ---- Streaming de la respuesta ----

    def render_text_chunk(self, text: str) -> None:
        self._switch_mode("text")
        self.console.print(text, style=_STYLE_TEXT, end="", soft_wrap=True, markup=False)

    def render_thinking_chunk(self, text: str) -> None:
        self._switch_mode("thinking")
        self.console.print(text, style=_STYLE_THINKING, end="", soft_wrap=True, markup=False)

    def end_stream(self) -> None:
        """Cerrar el bloque que se estaba streameando (si había uno)."""
        if self._stream_mode is not None:
            self.console.print()
            self._stream_mode = None

    def _switch_mode(self, mode: Literal["text", "thinking"]) -> None:
        if self._stream_mode not in (None, mode):
            self.console.print()
        self._stream_mode = mode

    # ---- Tool calls y resultados ----

    def render_tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Mostrar la tool que se va a ejecutar (después de aprobación)."""
        self.end_stream()
        main_key = _MAIN_ARG.get(name)
        parts: list[str] = []
        if main_key and main_key in args:
            parts.append(_truncate(str(args[main_key]), _ARG_MAX_LENGTH))
        parts += [
            f"{k}={_truncate(str(v), 40)}"
            for k, v in args.items()
            if k != main_key and k not in _HIDDEN_ARGS
        ]
        line = Text()
        line.append("▶ ", style=_STYLE_TOOL_ARGS)
        line.append(name, style=_STYLE_TOOL_NAME)
        if parts:
            line.append(" " + " ".join(parts), style=_STYLE_TOOL_ARGS)
        self.console.print(line)

    def render_observation(self, result: str, is_error: bool = False) -> None:
        """Mostrar (recortado) el resultado de una tool."""
        if is_error:
            self.console.print(Text("  ✗ " + _truncate(result, 300), style=_STYLE_ERROR))
            return
        body = _truncate_multiline(result, _OBSERVATION_MAX_LINES, _OBSERVATION_MAX_CHARS)
        lines = body.splitlines() or ["(vacío)"]
        rendered = "\n".join(
            ("  ← " if i == 0 else "    ") + line for i, line in enumerate(lines)
        )
        self.console.print(Text(rendered, style=_STYLE_OBSERVATION))

    # ---- Mensajes del sistema ----

    def render_error(self, message: str) -> None:
        self.end_stream()
        self.console.print(Text(f"✗ {message}", style=_STYLE_ERROR))

    def render_info(self, message: str) -> None:
        self.end_stream()
        self.console.print(Text(message, style="dim"))

    def render_separator(self) -> None:
        self.end_stream()
        self.console.rule(style="dim")

    @contextmanager
    def status(self, message: str) -> Iterator[None]:
        """Spinner mientras corre algo largo (una tool, la espera al modelo)."""
        self.end_stream()
        with self.console.status(Text(message, style="dim"), spinner="dots"):
            yield


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _truncate(value: str, max_length: int) -> str:
    value = value.replace("\n", "⏎ ")
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _truncate_multiline(text: str, max_lines: int, max_chars: int) -> str:
    """Truncar por líneas y por caracteres; lo más restrictivo gana."""
    lines = text.splitlines()
    extra = ""
    if len(lines) > max_lines:
        extra = f"\n… ({len(lines) - max_lines} líneas más)"
        lines = lines[:max_lines]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text + extra
