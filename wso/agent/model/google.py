"""Cliente para Google Gemini.

Usa la SDK oficial `google-generativeai` con streaming asíncrono.
La key se pasa en el constructor; el system prompt va separado del
historial de mensajes (convención de la API de Gemini).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient


class GoogleClient(ModelClient):
    """Cliente para Google Gemini API."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model_name = model
        try:
            import google.generativeai as genai
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'google-generativeai' no está instalado. "
                "Instalalo con: pip install google-generativeai"
            ) from e
        genai.configure(api_key=api_key)
        self._genai = genai

    @property
    def model_name(self) -> str:
        return self._model_name

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Llamar Gemini API en streaming.

        Convierte los `Message` de WSO al formato de Gemini:
            - role 'system' → system_instruction (param separado)
            - role 'user' → contents con role='user'
            - role 'assistant' → contents con role='model'

        Args:
            messages: Historial completo de la conversación.

        Yields:
            Chunks de texto a medida que el modelo los emite.

        Raises:
            ConnectionError: si la API falla o la key es inválida.
        """
        system_parts: list[str] = []
        contents: list[dict[str, object]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [msg.content]})
            elif msg.role == "assistant":
                # Gemini usa 'model' en lugar de 'assistant'
                contents.append({"role": "model", "parts": [msg.content]})

        system_instruction = "\n\n".join(system_parts) if system_parts else None

        model = self._genai.GenerativeModel(
            self._model_name,
            system_instruction=system_instruction,
        )

        try:
            response = await model.generate_content_async(contents, stream=True)
            async for chunk in response:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except Exception as e:
            raise ConnectionError(
                f"Error en Google Gemini API ({self._model_name}): {e}"
            ) from e
