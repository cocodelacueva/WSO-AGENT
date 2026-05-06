"""Renderer centralizado para la salida del agente.

Toda la presentación visual del agente pasa por este módulo. Si querés
cambiar el estilo (colores, indentación, símbolos), lo hacés acá sin
tocar lógica.

Convenciones de estilo (v1):
    thinking      → texto dim/italic (gris)
    tool call     → cyan, con prefijo "▶"
    observation   → texto normal, con prefijo "←"
    error         → rojo, con prefijo "✗"
    user input    → bold, con prefijo "›"
    permission    → amarillo, panel con borde
    success       → verde, con prefijo "✓"
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console


@dataclass
class ConsoleRenderer:
    """Wrapper del Console de Rich con métodos semánticos.

    Recibir esto como dependencia (en lugar de Console directo) hace
    fácil mockear en tests y cambiar estilos sin scatter.
    """

    console: Console

    def render_user_input(self, text: str) -> None:
        """Mostrar lo que el usuario escribió (echo o cuando se relee del historial)."""
        raise NotImplementedError

    def render_thinking_chunk(self, text: str) -> None:
        """Stream del thinking del modelo, chunk a chunk."""
        raise NotImplementedError

    def render_thinking_end(self) -> None:
        """Marcar el fin de un bloque de thinking (newline, separador)."""
        raise NotImplementedError

    def render_tool_call(self, name: str, args: dict[str, str]) -> None:
        """Mostrar la tool que se va a ejecutar (después de aprobación)."""
        raise NotImplementedError

    def render_observation(self, tool_name: str, result: str) -> None:
        """Mostrar el resultado de una tool ejecutada."""
        raise NotImplementedError

    def render_error(self, message: str) -> None:
        raise NotImplementedError

    def render_final_answer(self, message: str) -> None:
        """Mostrar responder_al_usuario con formato distintivo."""
        raise NotImplementedError
