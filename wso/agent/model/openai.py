"""Cliente para OpenAI GPT.

Usa la SDK oficial `openai` con streaming async.
También compatible con cualquier endpoint OpenAI-style cambiando
`base_url` (ej: Azure OpenAI, Together.ai, Groq, OpenRouter, etc).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient


class OpenAIClient(ModelClient):
    """Cliente para OpenAI Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_name = model
        self._base_url = base_url
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'openai' no está instalado. "
                "Instalalo con: pip install openai"
            ) from e
        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model_name

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Llamar OpenAI Chat Completions en streaming.

        Convierte los `Message` de WSO al formato de OpenAI: dicts
        con `role` y `content`. La estructura es prácticamente
        idéntica al formato neutral, así que es traducción directa.
        """
        api_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        try:
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=api_messages,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as e:
            raise ConnectionError(
                f"Error en OpenAI API ({self._model_name}): {e}"
            ) from e
