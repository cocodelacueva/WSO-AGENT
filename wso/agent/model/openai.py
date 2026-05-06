"""Cliente para OpenAI GPT (stub para v2).

Planeado para implementar usando la SDK oficial `openai` (modo
streaming con chat.completions). Compatible con cualquier endpoint
OpenAI-style cambiando base_url.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient


class OpenAIClient(ModelClient):
    """Cliente para OpenAI. Stub no implementado en v1."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    @property
    def model_name(self) -> str:
        return self._model

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        raise NotImplementedError("OpenAIClient planeado para v2.")
        if False:  # pragma: no cover
            yield ""
