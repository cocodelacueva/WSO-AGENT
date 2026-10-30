"""Rescate de tool calls escritas como texto.

Algunos modelos locales (qwen2.5-coder en particular) a veces escriben la
tool call en el texto en vez de usar el canal nativo:

    ```json
    {"name": "read_file", "arguments": {"path": "app.py"}}
    ```

o envuelta en `<tool_call>...</tool_call>`. Sin rescate, el loop lo
tomaría como respuesta final y el turno terminaría sin hacer nada.

Para no confundir una respuesta que *explica* JSON con una tool call, solo
rescatamos si el nombre es una tool conocida y el texto fuera del JSON es
corto (el mensaje es "básicamente" la llamada).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from wso.agent.model.base import ToolCall

_MAX_SURROUNDING_CHARS = 200
_NOISE = re.compile(r"```(?:json)?|</?tool_call>", re.IGNORECASE)


def salvage_tool_calls(text: str, known_tools: Iterable[str]) -> tuple[tuple[ToolCall, ...], str]:
    """Buscar tool calls en el texto.

    Returns:
        (tool calls encontradas, texto restante). Si no hay, ((), text).
    """
    known = set(known_tools)
    decoder = json.JSONDecoder()
    calls: list[ToolCall] = []
    spans: list[tuple[int, int]] = []

    i = 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        call = _as_call(obj, known, len(calls))
        if call is not None:
            calls.append(call)
            spans.append((i, end))
            i = end
        else:
            i += 1

    if not calls:
        return (), text

    rest = text
    for start, end in reversed(spans):
        rest = rest[:start] + rest[end:]
    rest = _NOISE.sub("", rest).strip()
    if len(rest) > _MAX_SURROUNDING_CHARS:
        return (), text
    return tuple(calls), rest


def _as_call(obj: object, known: set[str], n: int) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not isinstance(name, str) or name not in known:
        return None
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    return ToolCall(id=f"salvaged_{n}", name=name, args=args)
