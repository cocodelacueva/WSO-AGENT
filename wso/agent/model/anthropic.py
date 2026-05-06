"""Cliente para Anthropic Claude (stub para v2).

Planeado para implementar usando la SDK oficial `anthropic`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient


class AnthropicClient(ModelClient):
    """Cliente para Anthropic. Stub no implementado en v1."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        raise NotImplementedError("AnthropicClient planeado para v2.")
        if False:  # pragma: no cover
            yield ""
