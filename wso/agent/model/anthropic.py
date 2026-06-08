"""Cliente para Anthropic Claude.

Usa la SDK oficial `anthropic` con streaming async.
El system prompt va separado del historial de mensajes (convención
de la API de Anthropic).

Retry automático con exponential backoff: el SDK reintenta hasta 3 veces
ante errores transitorios (overloaded, rate limit, internal server error,
connection error). El usuario no se entera de los reintentos salvo que
fallen todos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wso.agent.model.base import Message, ModelClient

# Tokens máximos a generar por respuesta. La API de Anthropic
# requiere este parámetro. 8192 cubre thinking + tool calls largos
# sin truncar.
_DEFAULT_MAX_TOKENS = 8192

# Reintentos automáticos del SDK ante errores transitorios
# (overloaded_error, rate_limit, 5xx, connection errors). Cada reintento
# usa exponential backoff. 3 intentos cubre la mayoría de overloads de
# Anthropic sin alargar mucho la espera.
_DEFAULT_MAX_RETRIES = 3


class AnthropicClient(ModelClient):
    """Cliente para Anthropic Claude API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._model_name = model
        self._max_tokens = max_tokens
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'anthropic' no está instalado. "
                "Instalalo con: pip install anthropic"
            ) from e
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=max_retries,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    async def stream_chat(
        self,
        messages: list[Message],
    ) -> AsyncIterator[str]:
        """Llamar Anthropic API en streaming.

        Convierte los `Message` de WSO al formato de Anthropic:
            - role 'system' → system param (concatenado si hay varios)
            - role 'user' / 'assistant' → messages array

        Anthropic requiere que los mensajes alternen user/assistant
        empezando por user. Si vienen dos del mismo rol seguidos,
        los mergeamos para evitar errores de la API.
        """
        system_parts: list[str] = []
        api_messages: list[dict[str, str]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
                continue

            # Merge si el último mensaje es del mismo rol
            if api_messages and api_messages[-1]["role"] == msg.role:
                api_messages[-1]["content"] += "\n\n" + msg.content
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        system_prompt = "\n\n".join(system_parts) if system_parts else None

        try:
            kwargs: dict[str, object] = {
                "model": self._model_name,
                "max_tokens": self._max_tokens,
                "messages": api_messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except Exception as e:
            # Detectar overload para dar un mensaje accionable al usuario
            error_str = str(e)
            if "overloaded" in error_str.lower():
                hint = (
                    " Los servidores de Anthropic están saturados ahora "
                    "(no es problema de tu cuenta). Probá de nuevo en "
                    "unos minutos o cambiá a claude-haiku-4-5 o claude-sonnet-4-5 "
                    "que se saturan menos."
                )
            elif "rate_limit" in error_str.lower():
                hint = (
                    " Llegaste al rate limit de tu cuenta. Esperá un minuto "
                    "y reintentá."
                )
            elif "credit" in error_str.lower() or "billing" in error_str.lower():
                hint = (
                    " Falta crédito en tu cuenta Anthropic. "
                    "Cargá más en https://console.anthropic.com"
                )
            else:
                hint = ""
            raise ConnectionError(
                f"Error en Anthropic API ({self._model_name}): {e}.{hint}"
            ) from e
