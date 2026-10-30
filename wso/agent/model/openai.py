"""Cliente para OpenAI Chat Completions, con function calling nativo.

También sirve para cualquier endpoint OpenAI-compatible cambiando
`base_url`: OpenRouter, Together.ai, Groq, vLLM, LM Studio, y Gemini
(`https://generativelanguage.googleapis.com/v1beta/openai/`).
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
    ToolCall,
    ToolSpec,
    Usage,
)


class OpenAIClient(ModelClient):
    """Cliente para OpenAI y endpoints compatibles."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        context_window: int = 128_000,
    ) -> None:
        self._model_name = model
        self._context_window = context_window
        try:
            import openai  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "El paquete 'openai' no está instalado. Instalalo con: pip install openai"
            ) from e
        self._openai = openai
        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def context_window(self) -> int:
        return self._context_window

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> AsyncIterator[StreamEvent]:
        o = self._openai
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [to_openai_tool(t) for t in tools]

        text_parts: list[str] = []
        # index → {"id", "name", "arguments"} (los args llegan fragmentados)
        partial_calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        truncated = False
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage:
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = partial_calls.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
                if choice.finish_reason == "length":
                    truncated = True
        except (
            o.AuthenticationError,
            o.PermissionDeniedError,
            o.NotFoundError,
            o.BadRequestError,
        ) as e:
            raise ModelError(
                f"Error en OpenAI API ({self._model_name}): {e}", retryable=False
            ) from e
        except Exception as e:
            raise ModelError(f"Error en OpenAI API ({self._model_name}): {e}") from e

        calls = tuple(
            ToolCall(
                id=slot["id"] or f"call_{i}",
                name=slot["name"],
                args=_parse_args(slot["arguments"]),
            )
            for i, slot in sorted(partial_calls.items())
        )
        yield ResponseDone(
            message=Message(role="assistant", content="".join(text_parts), tool_calls=calls),
            usage=usage,
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Conversión de formatos (funciones puras, testeables)
# ---------------------------------------------------------------------------


def to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.args)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _parse_args(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
