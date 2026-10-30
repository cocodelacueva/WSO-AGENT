"""Tests de integración del AgentLoop con un modelo guionado.

Verifican el contrato del loop sin un modelo real: fin de turno por texto,
ejecución de tools, permisos, recuperación de errores y manejo de contexto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes import call, make_loop, roles, say
from wso.agent.model.base import Message, ModelError, ResponseDone, ToolCall


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")
    return proj


def tool_results(loop) -> list[Message]:
    return [m for m in loop.history if m.role == "tool"]


class TestTurnBasics:
    async def test_text_answer_ends_turn(self, project: Path) -> None:
        loop, model, out = make_loop(project, [say("Hola, ¿qué hacemos?")])
        await loop.execute_turn("hola")
        assert len(model.calls) == 1
        assert roles(loop.history) == ["user", "assistant"]
        assert "Hola, ¿qué hacemos?" in out.getvalue()

    async def test_sends_system_prompt_and_tool_specs(self, project: Path) -> None:
        loop, model, _ = make_loop(project, [say("ok")])
        await loop.execute_turn("hola")
        sent = model.calls[0]
        assert sent[0] == Message(role="system", content="SYSTEM")
        assert sent[1] == Message(role="user", content="hola")
        assert {t.name for t in model.tools_seen[0]} == {
            "read_file", "write_file", "edit_file", "glob", "grep", "bash",
        }

    async def test_read_tool_runs_and_result_goes_back(self, project: Path) -> None:
        loop, model, _ = make_loop(project, [call("read_file", path="a.py"), say("x vale 1")])
        await loop.execute_turn("¿cuánto vale x?")
        assert roles(loop.history) == ["user", "assistant", "tool", "assistant"]
        result = tool_results(loop)[0]
        assert result.tool_call_id == "c1"
        assert result.name == "read_file"
        assert "x = 1" in result.content
        # La segunda llamada al modelo ve el resultado.
        assert model.calls[1][-1] == result


class TestToolErrors:
    async def test_unknown_tool(self, project: Path) -> None:
        loop, _, _ = make_loop(project, [call("borrar_todo"), say("perdón")])
        await loop.execute_turn("x")
        result = tool_results(loop)[0]
        assert result.is_error
        assert "no existe" in result.content
        assert "read_file" in result.content

    async def test_invalid_args_returned_as_error(self, project: Path) -> None:
        loop, _, _ = make_loop(project, [call("read_file"), say("ok")])  # falta path
        await loop.execute_turn("x")
        result = tool_results(loop)[0]
        assert result.is_error
        assert "path" in result.content

    async def test_tool_exception_returned_as_error(self, project: Path) -> None:
        loop, _, _ = make_loop(project, [call("read_file", path="nope.py"), say("ok")])
        await loop.execute_turn("x")
        assert tool_results(loop)[0].content.startswith("ERROR: FileNotFoundError")


class TestPermissions:
    async def test_approve_once_writes(self, project: Path) -> None:
        loop, _, _ = make_loop(
            project, [call("write_file", path="b.py", content="y = 2\n"), say("listo")],
            answers=["y"],
        )
        await loop.execute_turn("creá b.py")
        assert (project / "b.py").read_text(encoding="utf-8") == "y = 2\n"

    async def test_deny_ends_turn_without_writing(self, project: Path) -> None:
        loop, model, _ = make_loop(
            project, [call("write_file", path="b.py", content="y"), say("no debería llegar")],
            answers=["n"],
        )
        await loop.execute_turn("creá b.py")
        assert not (project / "b.py").exists()
        assert len(model.calls) == 1
        assert "rechazó" in tool_results(loop)[0].content

    async def test_deny_with_feedback_continues(self, project: Path) -> None:
        loop, model, _ = make_loop(
            project, [call("write_file", path="b.py", content="y"), say("ok, lo cambio")],
            answers=["llamalo c.py"],
        )
        await loop.execute_turn("creá b.py")
        assert len(model.calls) == 2
        assert "llamalo c.py" in tool_results(loop)[0].content

    async def test_session_approval_covers_later_edits(self, project: Path) -> None:
        loop, _, _ = make_loop(
            project,
            [
                call("edit_file", "c1", path="a.py", old_string="x = 1", new_string="x = 2"),
                call("edit_file", "c2", path="a.py", old_string="x = 2", new_string="x = 3"),
                say("listo"),
            ],
            answers=["s"],  # una sola respuesta: la segunda edición no pregunta
        )
        await loop.execute_turn("cambiá x")
        assert (project / "a.py").read_text(encoding="utf-8") == "x = 3\n"

    async def test_deny_cancels_remaining_calls_of_same_response(self, project: Path) -> None:
        both = Message(
            role="assistant",
            tool_calls=(
                ToolCall("c1", "write_file", {"path": "b.py", "content": "1"}),
                ToolCall("c2", "read_file", {"path": "a.py"}),
            ),
        )
        loop, _, _ = make_loop(project, [both], answers=["n"])
        await loop.execute_turn("x")
        results = tool_results(loop)
        assert [r.tool_call_id for r in results] == ["c1", "c2"]
        assert "No se ejecutó" in results[1].content


class TestModelRobustness:
    async def test_salvages_tool_call_written_as_text(self, project: Path) -> None:
        text = '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```'
        loop, _, _ = make_loop(project, [say(text), say("listo")])
        await loop.execute_turn("leé a.py")
        assert roles(loop.history) == ["user", "assistant", "tool", "assistant"]
        assert loop.history[1].tool_calls[0].name == "read_file"
        assert "x = 1" in tool_results(loop)[0].content

    async def test_empty_response_nudged_once(self, project: Path) -> None:
        loop, model, _ = make_loop(project, [say(""), say(""), say("nunca")])
        await loop.execute_turn("x")
        assert len(model.calls) == 2
        assert "vacía" in loop.history[2].content

    async def test_retryable_error_is_retried(self, project: Path) -> None:
        loop, model, _ = make_loop(project, [ModelError("se cayó"), say("ok")])
        await loop.execute_turn("x")
        assert len(model.calls) == 2
        assert loop.history[-1].content == "ok"

    async def test_non_retryable_error_aborts_immediately(self, project: Path) -> None:
        loop, model, out = make_loop(project, [ModelError("sin crédito", retryable=False)])
        await loop.execute_turn("x")
        assert len(model.calls) == 1
        assert "sin crédito" in out.getvalue()

    async def test_aborts_after_three_consecutive_errors(self, project: Path) -> None:
        loop, model, _ = make_loop(project, [ModelError("e")] * 5)
        await loop.execute_turn("x")
        assert len(model.calls) == 3

    async def test_truncated_response_does_not_run_tools(self, project: Path) -> None:
        cut = ResponseDone(
            message=call("write_file", path="b.py", content="mitad"), truncated=True
        )
        loop, _, _ = make_loop(project, [cut, say("ok")])
        await loop.execute_turn("x")
        assert not (project / "b.py").exists()
        assert "se cortó" in tool_results(loop)[0].content

    async def test_interrupt_closes_pending_tool_calls(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop, _, out = make_loop(project, [call("read_file", path="a.py")])

        def boom(**_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(loop.tools.get("read_file"), "handler", boom)
        await loop.execute_turn("x")
        assert "interrumpido" in out.getvalue()
        assert tool_results(loop)[0].tool_call_id == "c1"


class TestBudget:
    async def test_stops_when_user_declines_to_continue(self, project: Path) -> None:
        script = [call("read_file", f"c{i}", path="a.py") for i in range(10)]
        loop, model, _ = make_loop(project, script, answers=["n"], step_budget=3)
        await loop.execute_turn("x")
        assert len(model.calls) == 3

    async def test_continue_extends_budget(self, project: Path) -> None:
        script = [call("read_file", f"c{i}", path="a.py") for i in range(4)] + [say("fin")]
        loop, model, _ = make_loop(project, script, answers=["y"], step_budget=3)
        await loop.execute_turn("x")
        assert loop.history[-1].content == "fin"


class TestContext:
    async def test_old_results_elided_when_window_is_small(self, project: Path) -> None:
        (project / "big.py").write_text("\n".join(f"v{i} = {i}" for i in range(200)))
        script = [call("read_file", f"c{i}", path="big.py") for i in range(6)] + [say("fin")]
        loop, model, out = make_loop(project, script, window=4000)
        await loop.execute_turn("x")
        results = tool_results(loop)
        assert results[0].content.startswith("[omitido")
        assert not results[-1].content.startswith("[omitido")
        assert "[contexto:" in out.getvalue()


class TestSessionLog:
    async def test_logs_main_events(self, project: Path, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        loop, _, _ = make_loop(
            project, [call("read_file", path="a.py"), say("ok")], log_dir=log_dir
        )
        await loop.execute_turn("x")
        loop.session_log.close()
        events = [
            json.loads(line)["event"]
            for f in log_dir.glob("*.jsonl")
            for line in f.read_text().splitlines()
        ]
        for expected in ("turn_start", "model_response", "tool_call", "permission",
                         "observation", "turn_end"):
            assert expected in events
