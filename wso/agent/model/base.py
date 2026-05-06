"""Interfaz abstracta para clientes de modelo.

Cualquier provider (Ollama local, Anthropic, OpenAI, Google) implementa
esta interfaz. El resto del agente solo conoce este contrato.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Message:
    """Mensaje en formato neutral (no atado a un provider específico)."""

    role: Literal["system", "user", "assistant"]
    content: str


class ModelClient(ABC):
    """Cliente de modelo agnóstico al provider.

    Las implementaciones concretas se eligen vía `factory.build_model_client`.
    """

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Stream tokens del modelo en respuesta a los mensajes dados.

        Args:
            messages: Historial completo (incluye system prompt como
                primer mensaje de rol "system").

        Yields:
            Chunks de texto a medida que el modelo los emite.

        Raises:
            ConnectionError: si el modelo no responde.
            ValueError: si los mensajes están malformados.
        """
        ...
        # nota: este 'yield' está acá solo para que mypy lo trate como
        # generator; las subclases lo override.
        if False:  # pragma: no cover
            yield ""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Nombre canónico del modelo (para logs y UI)."""
        ...
