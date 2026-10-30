"""Cliente para modelos locales vía Ollama, con tool calling nativo.

Ollama expone tool calling en `/api/chat` con el parámetro `tools` (formato
OpenAI de function calling). El template del modelo (Qwen, Llama, etc) se
encarga de formatear las tools como el modelo fue entrenado a verlas, y
Ollama devuelve las tool calls ya parseadas en `message.tool_calls`.

`num_ctx` controla el tamaño del contexto (y la VRAM del KV cache). Si el
prompt lo excede, Ollama recorta en silencio; por eso el loop mantiene el
historial por debajo de `context_window` (ver `agent/context.py`).
"""

from __future__ import annotations

import json
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


class OllamaClient(ModelClient):
    """Cliente para Ollama (local o en otra máquina de la red)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 16384,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._num_ctx = num_ctx
        try:
            import ollama  # noqa: PLC0415 — lazy: no cargar si WSO_MODE=cloud
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'ollama' no está instalado. Instalalo con: pip install ollama"
            ) from e
        self._client = ollama.AsyncClient(host=base_url)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._num_ctx

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> AsyncIterator[StreamEvent]:
        try:
            stream = await self._client.chat(
                model=self._model,
                messages=to_ollama_messages(messages),
                tools=[to_ollama_tool(t) for t in tools] or None,
                stream=True,
                options={"num_ctx": self._num_ctx},
            )
        except Exception as e:
            hint = ""
            if "does not support tools" in str(e):
                hint = " → Este modelo no soporta tool calling en Ollama; probá otro."
            raise ModelError(
                f"No se pudo conectar a Ollama en {self._base_url} "
                f"(modelo: {self._model}): {e}{hint}",
                retryable=_is_retryable(e),
            ) from e

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage = Usage()
        truncated = False
        try:
            async for chunk in stream:
                msg = chunk.message
                if msg.thinking:
                    yield ThinkingDelta(msg.thinking)
                if msg.content:
                    text_parts.append(msg.content)
                    yield TextDelta(msg.content)
                for tc in msg.tool_calls or []:
                    calls.append(
                        ToolCall(
                            id=f"call_{len(calls)}",
                            name=tc.function.name,
                            args=_coerce_args(tc.function.arguments),
                        )
                    )
                if chunk.done:
                    usage = Usage(
                        input_tokens=chunk.prompt_eval_count or 0,
                        output_tokens=chunk.eval_count or 0,
                    )
                    truncated = chunk.done_reason == "length"
        except Exception as e:
            raise ModelError(f"Error durante streaming de Ollama: {e}") from e

        yield ResponseDone(
            message=Message(
                role="assistant", content="".join(text_parts), tool_calls=tuple(calls)
            ),
            usage=usage,
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Conversión de formatos (funciones puras, testeables)
# ---------------------------------------------------------------------------


def to_ollama_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def to_ollama_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "content": m.content, "tool_name": m.name or ""})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.args}}
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _coerce_args(arguments: Any) -> dict[str, Any]:
    """Algunos modelos devuelven los args como string JSON en vez de objeto."""
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_retryable(e: Exception) -> bool:
    """Modelo inexistente (404) o sin soporte de tools (400) no se arreglan reintentando."""
    status = getattr(e, "status_code", None)
    return status not in (400, 404)
