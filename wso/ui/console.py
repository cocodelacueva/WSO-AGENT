"""Renderer centralizado para la salida del agente.

Toda la presentación visual del agente pasa por este módulo. Si querés
cambiar el estilo (colores, indentación, símbolos), lo hacés acá sin
tocar lógica del loop.

Convenciones de estilo:
    user input        → bold, prefijo "›"
    thinking          → dim italic (gris), streaming
    tool call         → cyan, prefijo "▶"
    observation       → texto normal, prefijo "←"
    error             → rojo, prefijo "✗"
    final answer      → panel verde
    pregunta al user  → panel cyan
    separator         → línea horizontal dim
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# ---------------------------------------------------------------------------
# Constantes de estilo
# ---------------------------------------------------------------------------


_STYLE_USER = "bold"
_STYLE_THINKING = "dim italic"
_STYLE_TOOL_NAME = "bold cyan"
_STYLE_TOOL_ARGS = "white"
_STYLE_TOOL_PREFIX = "cyan"
_STYLE_OBSERVATION_PREFIX = "dim"
_STYLE_OBSERVATION_BODY = "white"
_STYLE_ERROR = "red"
_STYLE_ANSWER_BORDER = "green"
_STYLE_QUESTION_BORDER = "cyan"

# Truncamiento de argumentos en el display de tool call
_ARG_MAX_LENGTH = 80
_OBSERVATION_MAX_CHARS = 600
_OBSERVATION_MAX_LINES = 12


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass
class ConsoleRenderer:
    """Wrapper del Console de Rich con métodos semánticos.

    Recibir esto como dependencia en el loop (en lugar de Console
    directo) facilita los tests y permite cambiar estilos sin scatter.
    """

    console: Console

    # ---- Input del usuario ----

    def render_user_input(self, text: str) -> None:
        """Echo de lo que tipeó el usuario (visualmente formateado)."""
        self.console.print(f"[{_STYLE_USER}]›[/] {text}")

    # ---- Streaming del thinking ----

    def render_thinking_chunk(self, text: str) -> None:
        """Emitir un chunk de thinking. NO agrega newline."""
        # `soft_wrap=True` evita que Rich corte palabras a mitad.
        # `end=""` impide newline para acumular chunks.
        self.console.print(text, style=_STYLE_THINKING, end="", soft_wrap=True)

    def render_thinking_end(self) -> None:
        """Cerrar un bloque de thinking con newline."""
        self.console.print()

    # ---- Tool calls y observaciones ----

    def render_tool_call(self, name: str, args: dict[str, str]) -> None:
        """Mostrar la tool que se va a ejecutar (después de aprobación)."""
        args_str = ", ".join(
            f"{k}={_truncate(str(v), _ARG_MAX_LENGTH)!r}"
            for k, v in args.items()
        )
        line = Text()
        line.append("▶ ", style=_STYLE_TOOL_PREFIX)
        line.append(name, style=_STYLE_TOOL_NAME)
        line.append(f"({args_str})", style=_STYLE_TOOL_ARGS)
        self.console.print(line)

    def render_observation(
        self,
        tool_name: str,
        result: str,
        is_error: bool = False,
    ) -> None:
        """Mostrar el resultado de una tool ejecutada.

        Args:
            tool_name: Nombre de la tool que ejecutó.
            result: Texto del resultado (o mensaje de error).
            is_error: Si True, formatea como error.
        """
        if is_error:
            self.console.print(f"[{_STYLE_ERROR}]✗[/] {result}")
            return

        truncated = _truncate_multiline(
            result, _OBSERVATION_MAX_LINES, _OBSERVATION_MAX_CHARS
        )
        prefix = Text("← ", style=_STYLE_OBSERVATION_PREFIX)
        prefix.append(truncated, style=_STYLE_OBSERVATION_BODY)
        self.console.print(prefix)

    # ---- Errores genéricos ----

    def render_error(self, message: str) -> None:
        """Error que no es de tool (ej: parser, model, config)."""
        self.console.print(f"[{_STYLE_ERROR}]✗ {message}[/]")

    # ---- Outputs terminales del turno ----

    def render_final_answer(self, message: str) -> None:
        """Mostrar la respuesta final cuando se invoca responder_al_usuario."""
        panel = Panel(
            Text(message),
            title="Respuesta",
            border_style=_STYLE_ANSWER_BORDER,
        )
        self.console.print(panel)

    def render_question(
        self,
        question: str,
        options: list[str],
        permite_respuesta_libre: bool,
    ) -> None:
        """Mostrar una pregunta al usuario (preguntar_al_usuario)."""
        body = Text()
        body.append(question + "\n", style="bold")

        if options:
            body.append("\nOpciones:\n", style="dim")
            for i, opt in enumerate(options, 1):
                body.append(f"  {i}. {opt}\n")

        if permite_respuesta_libre:
            body.append(
                "\n(o respondé con texto libre)\n",
                style="dim italic",
            )

        panel = Panel(
            body,
            title="❓ Pregunta",
            border_style=_STYLE_QUESTION_BORDER,
        )
        self.console.print(panel)

    # ---- Utilidades ----

    def render_separator(self) -> None:
        """Línea horizontal entre turnos."""
        self.console.rule(style="dim")

    def render_info(self, message: str) -> None:
        """Mensaje informativo del sistema (no del agente)."""
        self.console.print(f"[dim]{message}[/]")


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _truncate(value: str, max_length: int) -> str:
    """Truncar un string con elipsis si excede max_length."""
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _truncate_multiline(text: str, max_lines: int, max_chars: int) -> str:
    """Truncar por líneas Y caracteres. Lo más restrictivo gana.

    Diseñado para observations donde la salida puede ser larga (un
    archivo entero, output de list_directory, etc.).
    """
    lines = text.splitlines()
    line_truncated = False
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines])
        line_truncated = True
        suffix = f"\n  … ({len(lines) - max_lines} líneas más)"
        text += suffix

    if len(text) > max_chars and not line_truncated:
        return text[: max_chars - 1] + "…"

    return text
