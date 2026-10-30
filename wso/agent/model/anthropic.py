"""Cliente para Anthropic Claude, con tool use nativo y prompt caching.

- Tools: se mandan como `tools` (JSON Schema). Las respuestas traen bloques
  `tool_use`; los resultados vuelven como bloques `tool_result`.
- Caching: breakpoint en el system prompt (cubre tools + system, que no
  cambian en la sesión) y caching automático del último bloque de mensajes,
  así cada paso del loop paga solo lo nuevo.
- Thinking: adaptive con display "summarized" para ver el razonamiento en
  vivo. El contenido nativo de la respuesta (incluidos los bloques de
  thinking) se guarda en `Message.raw` y se devuelve intacto, como exige
  la API.
- Retries: el SDK reintenta solo errores transitorios (429, 5xx, red).
  Errores de request/auth/crédito se marcan como no reintentables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from wso.agent.model.base import (
    Message,
    ModelClient,
    ModelError,
    ResponseDone,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

# Streaming: no hay riesgo de timeout HTTP, damos aire para escribir archivos.
_DEFAULT_MAX_TOKENS = 32000
_DEFAULT_MAX_RETRIES = 3
_CACHE = {"type": "ephemeral"}


class AnthropicClient(ModelClient):
    """Cliente para la Claude API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        thinking: bool = True,
        effort: str | None = "high",
    ) -> None:
        self._model_name = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._effort = effort
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'anthropic' no está instalado. "
                "Instalalo con: pip install anthropic"
            ) from e
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def context_window(self) -> int:
        return 1_000_000

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> AsyncIterator[StreamEvent]:
        system, api_messages = to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._max_tokens,
            "messages": api_messages,
            "cache_control": _CACHE,
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": _CACHE}]
        if tools:
            kwargs["tools"] = [to_anthropic_tool(t) for t in tools]
        if self._thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}

        a = self._anthropic
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield TextDelta(event.text)
                    elif event.type == "thinking":
                        yield ThinkingDelta(event.thinking)
                final = await stream.get_final_message()
        except ValueError as e:
            # Tool input con JSON imposible de parsear: reintentar el paso.
            raise ModelError(f"Claude emitió una tool call ilegible: {e}") from e
        except (
            a.AuthenticationError,
            a.PermissionDeniedError,
            a.NotFoundError,
            a.BadRequestError,
        ) as e:
            raise ModelError(
                f"Error en Anthropic API ({self._model_name}): {e}{_hint(e)}",
                retryable=False,
            ) from e
        except (a.APIStatusError, a.APIConnectionError) as e:
            raise ModelError(
                f"Error en Anthropic API ({self._model_name}): {e}{_hint(e)}"
            ) from e

        if final.stop_reason == "refusal":
            raise ModelError(
                "Claude rechazó la solicitud (stop_reason=refusal).", retryable=False
            )

        text = "".join(b.text for b in final.content if b.type == "text")
        calls = tuple(
            ToolCall(id=b.id, name=b.name, args=dict(b.input) if isinstance(b.input, dict) else {})
            for b in final.content
            if b.type == "tool_use"
        )
        usage = final.usage
        yield ResponseDone(
            message=Message(
                role="assistant",
                content=text,
                tool_calls=calls,
                raw=[b.to_dict() for b in final.content],
            ),
            usage=Usage(
                input_tokens=(usage.input_tokens or 0)
                + (usage.cache_read_input_tokens or 0)
                + (usage.cache_creation_input_tokens or 0),
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
            ),
            truncated=final.stop_reason == "max_tokens",
        )


# ---------------------------------------------------------------------------
# Conversión de formatos (funciones puras, testeables)
# ---------------------------------------------------------------------------


def to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters,
        # Inputs grandes (write_file) llegan en streaming en vez de en un bloque.
        # El parser del SDK es tolerante: la validación real la hace @tool.
        "eager_input_streaming": True,
    }


def to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Separar el system prompt y convertir el resto a mensajes de Anthropic.

    Los resultados de tools consecutivos se agrupan en un único mensaje
    user (la API espera todos los `tool_result` de un turno juntos).
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue

        if m.role == "assistant":
            content: list[dict[str, Any]]
            if isinstance(m.raw, list):  # contenido nativo de Claude (otro provider: dict)
                content = list(m.raw)
            else:
                content = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                content.extend(
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in m.tool_calls
                )
            out.append({"role": "assistant", "content": content or [{"type": "text", "text": "."}]})
            continue

        if m.role == "tool":
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content,
            }
            if m.is_error:
                block["is_error"] = True
        else:
            block = {"type": "text", "text": m.content}

        # tool results y texto del usuario van en mensajes 'user'; si el
        # anterior ya es 'user', se agregan como bloques del mismo mensaje.
        if out and out[-1]["role"] == "user":
            out[-1]["content"].append(block)
        else:
            out.append({"role": "user", "content": [block]})

    return "\n\n".join(system_parts), out


def _hint(e: Exception) -> str:
    """Pista accionable para errores comunes."""
    text = str(e).lower()
    if "credit" in text or "billing" in text:
        return " → Falta crédito en la cuenta: https://console.anthropic.com"
    if "overloaded" in text:
        return " → Servidores saturados; probá en unos minutos."
    if "rate_limit" in text:
        return " → Rate limit; esperá un minuto."
    if "thinking" in text or "effort" in text:
        return (
            " → Este modelo puede no soportar thinking adaptive/effort: "
            "probá WSO_ANTHROPIC_THINKING=false o WSO_ANTHROPIC_EFFORT= (vacío)."
        )
    return ""
