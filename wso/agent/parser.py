"""Parser XML streaming tolerante para la salida del modelo.

Consume tokens uno a uno (a medida que llegan del modelo) y emite
eventos estructurados para que el loop reaccione en tiempo real.

Eventos emitidos:
    - ThinkingStart / ThinkingChunk / ThinkingEnd
    - ToolCallDetected (con nombre, antes de tener args completos)
    - ToolCallComplete (con args parseados, listos para ejecutar)
    - PlainTextChunk (texto fuera de cualquier tag — se loguea pero
      no se interpreta; el modelo debe usar responder_al_usuario)

Tolerancia esperada:
    - whitespace inconsistente alrededor de tags
    - atributos en cualquier orden
    - tags hijos en cualquier orden
    - tags mal cerrados se intentan recuperar
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto


class ParseState(Enum):
    """Estados de la máquina."""

    OUTSIDE = auto()
    IN_THINKING = auto()
    IN_TOOL = auto()


@dataclass
class ThinkingChunk:
    text: str


@dataclass
class ThinkingEnd:
    pass


@dataclass
class ToolCallComplete:
    name: str
    args: dict[str, str]


@dataclass
class PlainTextChunk:
    text: str


ParseEvent = ThinkingChunk | ThinkingEnd | ToolCallComplete | PlainTextChunk


class StreamingXMLParser:
    """Parser de estado para la salida del modelo.

    TODO(v1): implementar la máquina de estados completa.
    Diseño previsto:
      - mantener un buffer de texto que se va consumiendo
      - cada feed() emite cero o más eventos
      - finalize() consume cualquier resto y cierra estados pendientes
    """

    def __init__(self) -> None:
        self.state: ParseState = ParseState.OUTSIDE
        self._buffer: str = ""

    def feed(self, chunk: str) -> Iterator[ParseEvent]:
        """Consumir un chunk de tokens y emitir eventos.

        Yields:
            Eventos estructurados a medida que se completan.
        """
        raise NotImplementedError("Parser pendiente de implementar.")

    def finalize(self) -> Iterator[ParseEvent]:
        """Cerrar la sesión de parsing — flush de cualquier estado abierto."""
        raise NotImplementedError("Parser pendiente de implementar.")
