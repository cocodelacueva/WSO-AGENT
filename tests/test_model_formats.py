"""Tests de la conversión del formato neutral a cada provider."""

from __future__ import annotations

import json

from wso.agent.model.anthropic import to_anthropic_messages, to_anthropic_tool
from wso.agent.model.base import Message, ToolCall, ToolSpec
from wso.agent.model.ollama import _coerce_args, to_ollama_messages
from wso.agent.model.openai import _parse_args, to_openai_messages, to_openai_tool

CALL = ToolCall("id1", "read_file", {"path": "a.py"})
CONVERSATION = [
    Message(role="system", content="SYS"),
    Message(role="user", content="leé a.py"),
    Message(role="assistant", content="voy", tool_calls=(CALL,)),
    Message(role="tool", content="x = 1", tool_call_id="id1", name="read_file"),
    Message(role="assistant", content="x vale 1"),
]
SPEC = ToolSpec("read_file", "Lee", {"type": "object", "properties": {}})


class TestAnthropic:
    def test_system_split_and_tool_blocks(self) -> None:
        system, msgs = to_anthropic_messages(CONVERSATION)
        assert system == "SYS"
        assert msgs[0] == {"role": "user", "content": [{"type": "text", "text": "leé a.py"}]}
        assert msgs[1]["content"][1] == {
            "type": "tool_use", "id": "id1", "name": "read_file", "input": {"path": "a.py"},
        }
        assert msgs[2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "id1", "content": "x = 1"}],
        }

    def test_consecutive_tool_results_grouped(self) -> None:
        history = [
            Message(role="user", content="u"),
            Message(role="assistant", tool_calls=(CALL, ToolCall("id2", "bash", {}))),
            Message(role="tool", content="a", tool_call_id="id1", name="read_file"),
            Message(role="tool", content="b", tool_call_id="id2", name="bash", is_error=True),
        ]
        _, msgs = to_anthropic_messages(history)
        assert len(msgs) == 3
        assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["id1", "id2"]
        assert msgs[2]["content"][1]["is_error"] is True

    def test_raw_content_passed_back_untouched(self) -> None:
        raw = [
            {"type": "thinking", "thinking": "…", "signature": "sig"},
            {"type": "text", "text": "hola"},
        ]
        history = [
            Message(role="user", content="u"),
            Message(role="assistant", content="hola", raw=raw),
        ]
        _, msgs = to_anthropic_messages(history)
        assert msgs[1]["content"] == raw

    def test_tool_schema(self) -> None:
        tool = to_anthropic_tool(SPEC)
        assert tool["input_schema"] == SPEC.parameters
        assert tool["eager_input_streaming"] is True


class TestOpenAI:
    def test_messages(self) -> None:
        msgs = to_openai_messages(CONVERSATION)
        assert msgs[0] == {"role": "system", "content": "SYS"}
        assert msgs[2]["tool_calls"][0]["function"] == {
            "name": "read_file", "arguments": json.dumps({"path": "a.py"}),
        }
        assert msgs[3] == {"role": "tool", "tool_call_id": "id1", "content": "x = 1"}

    def test_tool_schema(self) -> None:
        assert to_openai_tool(SPEC)["function"]["parameters"] == SPEC.parameters

    def test_parse_args_tolerant(self) -> None:
        assert _parse_args('{"a": 1}') == {"a": 1}
        assert _parse_args("") == {}
        assert _parse_args("{roto") == {}


class TestOllama:
    def test_messages(self) -> None:
        msgs = to_ollama_messages(CONVERSATION)
        assert msgs[2]["tool_calls"] == [
            {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
        ]
        assert msgs[3] == {"role": "tool", "content": "x = 1", "tool_name": "read_file"}

    def test_coerce_args(self) -> None:
        assert _coerce_args({"a": 1}) == {"a": 1}
        assert _coerce_args('{"a": 1}') == {"a": 1}
        assert _coerce_args("nope") == {}


class TestMessageSerialization:
    def test_roundtrip(self) -> None:
        for m in CONVERSATION:
            assert Message.from_dict(m.to_dict()) == m


class TestThinkingRoundtrip:
    def test_ollama_resends_thinking(self) -> None:
        m = Message(role="assistant", tool_calls=(CALL,), raw={"thinking": "plan"})
        assert to_ollama_messages([m])[0]["thinking"] == "plan"

    def test_anthropic_ignores_foreign_raw(self) -> None:
        history = [Message(role="user", content="u"),
                   Message(role="assistant", content="hola", raw={"thinking": "plan"})]
        _, msgs = to_anthropic_messages(history)
        assert msgs[1]["content"] == [{"type": "text", "text": "hola"}]
