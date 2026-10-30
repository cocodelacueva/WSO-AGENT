"""Tests del ContextManager."""

from __future__ import annotations

from wso.agent.context import ContextManager
from wso.agent.model.base import Message, ToolCall


def turn(i: int, result_chars: int = 3000) -> list[Message]:
    return [
        Message(role="user", content=f"pedido {i}"),
        Message(role="assistant", tool_calls=(ToolCall(f"c{i}", "read_file", {"path": "a"}),)),
        Message(role="tool", content="x" * result_chars, tool_call_id=f"c{i}", name="read_file"),
        Message(role="assistant", content=f"respuesta {i}"),
    ]


class TestFit:
    def test_noop_below_threshold(self) -> None:
        cm = ContextManager(window=100_000, reserve_output=1000)
        history = turn(1)
        report = cm.fit("sys", [], history)
        assert not report.changed
        assert history == turn(1)

    def test_elides_old_results_first_keeping_recent(self) -> None:
        cm = ContextManager(window=4000, reserve_output=0, keep_recent=2)
        history = [m for i in range(4) for m in turn(i)]  # 4 resultados de ~1000 tokens
        report = cm.fit("sys", [], history)
        tools = [m for m in history if m.role == "tool"]
        assert report.elided >= 1
        assert report.dropped == 0
        assert tools[0].content.startswith("[omitido")
        assert tools[-1].content == "x" * 3000
        # El resumen conserva el vínculo con la tool call.
        assert tools[0].tool_call_id == "c0"

    def test_drops_old_turns_when_eliding_is_not_enough(self) -> None:
        cm = ContextManager(window=500, reserve_output=0, keep_recent=0)
        history = [m for i in range(10) for m in turn(i, result_chars=100)]
        report = cm.fit("sys", [], history)
        assert report.dropped > 0
        assert history[0].role == "user"
        assert history[-1].content == "respuesta 9"  # el turno actual queda

    def test_never_drops_current_turn(self) -> None:
        cm = ContextManager(window=500, reserve_output=0)
        history = turn(0, result_chars=10_000)
        cm.fit("sys", [], history)
        assert [m.role for m in history] == ["user", "assistant", "tool", "assistant"]

    def test_for_window_reserves_output(self) -> None:
        cm = ContextManager.for_window(16384)
        assert cm.reserve_output == 4096
        assert cm.budget == 16384 - 4096
