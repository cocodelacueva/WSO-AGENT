"""Cliente para modelos locales vía Ollama.

Usa la librería oficial `ollama` con su API async de chat streaming.
Convierte los `Message` de WSO al formato dict que Ollama espera y
devuelve los chunks de texto a medida que llegan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from wso.agent.model.base import Message, ModelClient


class OllamaClient(ModelClient):
    """Cliente para Ollama local."""

    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 8192,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._num_ctx = num_ctx
        # Import lazy: evita el costo de importar ollama si nunca se usa
        # (ej: cuando WSO_MODE=cloud).
        try:
            import ollama
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'ollama' no está instalado. "
                "Instalalo con: pip install ollama"
            ) from e
        self._client = ollama.AsyncClient(host=base_url)

    @property
    def model_name(self) -> str:
        return self._model

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Llamar Ollama en streaming y emitir chunks de texto.

        Args:
            messages: Historial completo, primero `system`, luego pares
                `user`/`assistant`.

        Yields:
            Chunks del campo `message.content` a medida que llegan.

        Raises:
            ConnectionError: si Ollama no responde o el modelo no existe.
        """
        ollama_messages: list[dict[str, str]] = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        try:
            stream = await self._client.chat(
                model=self._model,
                messages=ollama_messages,
                stream=True,
                options={"num_ctx": self._num_ctx},
            )
        except Exception as e:
            raise ConnectionError(
                f"No se pudo conectar a Ollama en {self._base_url} "
                f"(modelo: {self._model}): {e}"
            ) from e

        try:
            async for chunk in stream:
                content = _extract_content(chunk)
                if content:
                    yield content
        except Exception as e:
            raise ConnectionError(f"Error durante streaming de Ollama: {e}") from e


def _extract_content(chunk: Any) -> str:
    """Extraer el campo `message.content` de un chunk de Ollama.

    El cliente puede devolver dicts o objetos según versión. Este helper
    tolera ambas formas.
    """
    if hasattr(chunk, "message"):
        message = chunk.message
        return getattr(message, "content", "") or ""
    if isinstance(chunk, dict):
        return chunk.get("message", {}).get("content", "") or ""
    return ""
