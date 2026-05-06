"""Cliente para modelos locales vía Ollama.

Usa la librería oficial `ollama` con su API nativa de chat streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient


class OllamaClient(ModelClient):
    """Cliente para Ollama local."""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url
        self._model = model
        # TODO(v1): instanciar ollama.AsyncClient(host=base_url)

    @property
    def model_name(self) -> str:
        return self._model

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Llamar Ollama en streaming y emitir chunks de texto.

        TODO(v1): implementar.
        Diseño previsto:
            - convertir Message[] al formato de ollama (lista de dicts
              con keys 'role' y 'content')
            - llamar self._client.chat(model=..., messages=...,
              stream=True)
            - iterar la respuesta y yield chunk['message']['content']
              de cada delta
        """
        raise NotImplementedError("OllamaClient pendiente de implementar.")
        if False:  # pragma: no cover
            yield ""
