"""Tests del BudgetTracker y prompt de continuación."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import StringIO

import pytest
from rich.console import Console

from wso.agent.budget import (
    BudgetTracker,
    ContinuationResponse,
    StepRecord,
    ask_continuation,
    parse_continuation_input,
    render_continuation_panel,
)


@pytest.fixture
def quiet_console() -> Console:
    return Console(file=StringIO(), force_terminal=False, no_color=True, width=85)


# ---------------------------------------------------------------------------
# BudgetTracker
# ---------------------------------------------------------------------------


class TestBudgetTracker:
    def test_initial_state(self) -> None:
        tracker = BudgetTracker()
        assert tracker.limit == 10
        assert tracker.used == 0
        assert tracker.history == []
        assert tracker.original_goal == ""
        assert not tracker.is_exhausted()

    def test_start_turn_resets_and_sets_goal(self) -> None:
        tracker = BudgetTracker()
        tracker.record(StepRecord("x", "y", True))
        tracker.start_turn(goal="nuevo objetivo")

        assert tracker.used == 0
        assert tracker.history == []
        assert tracker.original_goal == "nuevo objetivo"

    def test_start_turn_with_custom_limit(self) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x", limit=5)
        assert tracker.limit == 5

    def test_record_increments_counter(self) -> None:
        tracker = BudgetTracker()
        tracker.record(StepRecord("read_file", "path='/x'", True))
        assert tracker.used == 1
        assert len(tracker.history) == 1

    def test_is_exhausted_when_used_reaches_limit(self) -> None:
        tracker = BudgetTracker(limit=3)
        for _ in range(3):
            tracker.record(StepRecord("x", "y", True))
        assert tracker.is_exhausted()

    def test_extend_increases_limit(self) -> None:
        tracker = BudgetTracker(limit=10)
        for _ in range(10):
            tracker.record(StepRecord("x", "y", True))
        assert tracker.is_exhausted()

        tracker.extend(5)
        assert tracker.limit == 15
        assert not tracker.is_exhausted()

    def test_reset_clears_state(self) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")
        tracker.record(StepRecord("a", "b", True))
        tracker.reset()

        assert tracker.used == 0
        assert tracker.history == []
        assert tracker.original_goal == ""


# ---------------------------------------------------------------------------
# parse_continuation_input
# ---------------------------------------------------------------------------


class TestParseContinuationInput:
    @pytest.mark.parametrize("raw", ["y", "Y", " y ", "y\n"])
    def test_y_means_continue(self, raw: str) -> None:
        result = parse_continuation_input(raw)
        assert result.decision == "continue"
        assert result.feedback is None

    @pytest.mark.parametrize("raw", ["", " ", "\t", "n", "N", " n "])
    def test_empty_or_n_means_abort(self, raw: str) -> None:
        result = parse_continuation_input(raw)
        assert result.decision == "abort"
        assert result.feedback is None

    def test_other_text_aborts_with_feedback(self) -> None:
        result = parse_continuation_input("usá otro path")
        assert result.decision == "abort"
        assert result.feedback == "usá otro path"

    def test_feedback_is_stripped(self) -> None:
        result = parse_continuation_input("   probá distinto   ")
        assert result.decision == "abort"
        assert result.feedback == "probá distinto"


# ---------------------------------------------------------------------------
# render_continuation_panel
# ---------------------------------------------------------------------------


class TestRenderContinuationPanel:
    def test_panel_includes_goal(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="generar reporte Q3")
        for _ in range(10):
            tracker.record(StepRecord("read_file", "path='/x'", True))

        render_continuation_panel(tracker, quiet_console)

        output = quiet_console.file.getvalue()  # type: ignore[union-attr]
        assert "generar reporte Q3" in output

    def test_panel_shows_history_with_markers(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")
        tracker.record(StepRecord("read_file", "path='/a'", True))
        tracker.record(
            StepRecord("write_file", "path='/b'", False, error="Permission denied")
        )

        render_continuation_panel(tracker, quiet_console)
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]

        assert "read_file" in output
        assert "write_file" in output
        assert "Permission denied" in output
        # Markers de éxito y fallo
        assert "✓" in output
        assert "✗" in output

    def test_panel_includes_options(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")
        render_continuation_panel(tracker, quiet_console)
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]

        assert "continuar" in output.lower()
        assert "abortar" in output.lower()
        assert "feedback" in output.lower()

    def test_panel_with_current_thinking(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")
        render_continuation_panel(
            tracker,
            quiet_console,
            current_thinking="Voy a probar con otro path absoluto",
        )
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]

        assert "otro path absoluto" in output

    def test_panel_with_empty_history(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        render_continuation_panel(tracker, quiet_console)
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]

        assert "sin acciones registradas" in output


# ---------------------------------------------------------------------------
# ask_continuation
# ---------------------------------------------------------------------------


class TestAskContinuation:
    def test_returns_decision_from_input(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")

        response = ask_continuation(
            tracker,
            console=quiet_console,
            input_func=lambda prompt: "y",
        )
        assert response.decision == "continue"

    def test_renders_panel_to_console(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="objetivo X")

        ask_continuation(
            tracker,
            console=quiet_console,
            input_func=lambda prompt: "n",
        )
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]
        assert "Budget agotado" in output
        assert "objetivo X" in output

    def test_feedback_propagates(self, quiet_console: Console) -> None:
        tracker = BudgetTracker()
        tracker.start_turn(goal="x")

        response = ask_continuation(
            tracker,
            console=quiet_console,
            input_func=lambda prompt: "abandoná y reintentá con /tmp",
        )
        assert response.decision == "abort"
        assert response.feedback == "abandoná y reintentá con /tmp"


# ---------------------------------------------------------------------------
# ContinuationResponse semantics
# ---------------------------------------------------------------------------


class TestContinuationResponse:
    def test_is_frozen(self) -> None:
        response = ContinuationResponse(decision="continue")
        with pytest.raises(FrozenInstanceError):
            response.decision = "abort"  # type: ignore[misc]
