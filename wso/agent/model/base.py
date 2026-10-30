"""Interfaz abstracta para clientes de modelo.

Cualquier provider (Ollama local, Anthropic, OpenAI y compatibles)
implementa esta interfaz. El resto del agente solo conoce este contrato.

Tool calling nativo
-------------------
El loop le pasa al cliente la lista de `ToolSpec` (JSON Schema) y el
cliente usa el mecanismo de tool calling propio del provider. El modelo
devuelve las tool calls ya estructuradas (`ToolCall`), sin parsear texto.

Mensajes
--------
`Message` es el formato neutral. Roles:
    - system:    system prompt (siempre el primero).
    - user:      lo que escribe el usuario.
    - assistant: respuesta del modelo; puede traer `tool_calls`.
    - tool:      resultado de una tool call (`tool_call_id` la referencia).

`raw` guarda el contenido nativo del provider para la respuesta del
assistant cuando hay que devolverlo intacto (ej: los bloques de thinking
de Anthropic, que la API exige recibir sin modificar).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    """Una tool call pedida por el modelo."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """Definición de una tool en formato JSON Schema, para el provider."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """Mensaje en formato neutral (no atado a un provider específico)."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    """Solo assistant: tool calls pedidas en esta respuesta."""
    tool_call_id: str | None = None
    """Solo tool: id de la tool call que este mensaje responde."""
    name: str | None = None
    """Solo tool: nombre de la tool (Ollama lo necesita)."""
    is_error: bool = False
    """Solo tool: True si la tool falló."""
    raw: Any = None
    """Solo assistant: contenido nativo del provider (JSON-serializable)."""

    def to_dict(self) -> dict[str, Any]:
        """Serializar (para persistencia de historial y logs)."""
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": c.id, "name": c.name, "args": c.args} for c in self.tool_calls
            ]
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            data["name"] = self.name
        if self.is_error:
            data["is_error"] = True
        if self.raw is not None:
            data["raw"] = self.raw
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Inversa de `to_dict`. Lanza KeyError/TypeError si está malformado."""
        calls = tuple(
            ToolCall(id=c["id"], name=c["name"], args=dict(c["args"]))
            for c in data.get("tool_calls", [])
        )
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            tool_calls=calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            is_error=bool(data.get("is_error", False)),
            raw=data.get("raw"),
        )


# ---------------------------------------------------------------------------
# Eventos del stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """Fragmento de texto de la respuesta (lo que el usuario lee)."""

    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """Fragmento de razonamiento visible (si el modelo/provider lo expone)."""

    text: str


@dataclass(frozen=True)
class Usage:
    """Tokens reportados por el provider para una llamada."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class ResponseDone:
    """Último evento del stream: la respuesta completa ya armada."""

    message: Message
    """Mensaje del assistant (texto + tool calls + raw), listo para el historial."""
    usage: Usage = field(default_factory=Usage)
    truncated: bool = False
    """True si el modelo cortó por límite de tokens de salida."""


StreamEvent = TextDelta | ThinkingDelta | ResponseDone


class ModelError(Exception):
    """Error del provider. `retryable=False` → no tiene sentido reintentar."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelClient(ABC):
    """Cliente de modelo agnóstico al provider.

    Las implementaciones concretas se eligen vía `factory.build_model_client`.
    """

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> AsyncIterator[StreamEvent]:
        """Llamar al modelo en streaming.

        Yields:
            `TextDelta` / `ThinkingDelta` a medida que llegan, y un único
            `ResponseDone` al final.

        Raises:
            ModelError: si el provider falla.
        """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Nombre canónico del modelo (para logs y UI)."""

    @property
    def context_window(self) -> int:
        """Tokens de contexto disponibles (para el manejo de contexto)."""
        return 200_000
