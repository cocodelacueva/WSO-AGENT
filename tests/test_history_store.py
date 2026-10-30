"""Tests de HistoryStore (persistencia de historial) + integración en el loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes import call, make_loop, say
from wso.agent.model.base import Message, ToolCall
from wso.history_store import HistoryStore


def _msgs(*pairs: tuple[str, str]) -> list[Message]:
    return [Message(role=r, content=c) for r, c in pairs]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit: save / load / clear
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_roundtrip(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        store.save(_msgs(("user", "hola"), ("assistant", "qué tal")))
        loaded = store.load()
        assert [(m.role, m.content) for m in loaded] == [
            ("user", "hola"),
            ("assistant", "qué tal"),
        ]

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert HistoryStore(path=tmp_path / "nope.json").load() == []

    def test_system_messages_not_persisted(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        store.save(_msgs(("system", "SYS"), ("user", "u"), ("assistant", "a")))
        roles = [m.role for m in store.load()]
        assert "system" not in roles
        assert roles == ["user", "assistant"]

    def test_cap_keeps_last_n_starting_at_user(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json", max_messages=3)
        store.save(
            _msgs(("user", "1"), ("assistant", "2"), ("user", "3"), ("assistant", "4"))
        )
        # Los últimos 3 serían [2, 3, 4], pero no se arranca en un assistant.
        assert [m.content for m in store.load()] == ["3", "4"]

    def test_roundtrip_tool_calls(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        history = [
            Message(role="user", content="leé a.py"),
            Message(
                role="assistant",
                tool_calls=(ToolCall("c1", "read_file", {"path": "a.py"}),),
                raw=[{"type": "tool_use", "id": "c1"}],
            ),
            Message(role="tool", content="x", tool_call_id="c1", name="read_file", is_error=True),
            Message(role="assistant", content="listo"),
        ]
        store.save(history)
        assert store.load() == history

    def test_cap_applied_on_load_too(self, tmp_path: Path) -> None:
        # Archivo con más mensajes que el cap actual → load recorta.
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                {"messages": [{"role": "user", "content": str(i)} for i in range(10)]}
            ),
            encoding="utf-8",
        )
        loaded = HistoryStore(path=p, max_messages=3).load()
        assert [m.content for m in loaded] == ["7", "8", "9"]

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        store = HistoryStore(path=p)
        store.save(_msgs(("user", "x")))
        assert p.exists()
        store.clear()
        assert not p.exists()
        assert store.load() == []

    def test_atomic_no_tmp_left_behind(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        HistoryStore(path=p).save(_msgs(("user", "x")))
        assert not (tmp_path / "h.json.tmp").exists()


# ---------------------------------------------------------------------------
# Unit: no-op y robustez
# ---------------------------------------------------------------------------


class TestNoOpAndRobustness:
    def test_noop_when_path_none(self, tmp_path: Path) -> None:
        store = HistoryStore(path=None)
        assert not store.enabled
        store.save(_msgs(("user", "x")))  # no explota
        assert store.load() == []

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text("{ no es json válido", encoding="utf-8")
        assert HistoryStore(path=p).load() == []

    def test_non_dict_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert HistoryStore(path=p).load() == []

    def test_malformed_messages_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "ok"},
                        {"role": "user"},  # sin content
                        {"content": "sin role"},
                        "no es dict",
                        {"role": "robot", "content": "rol inválido"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        loaded = HistoryStore(path=p).load()
        assert [(m.role, m.content) for m in loaded] == [("user", "ok"), ("user", "")]


# ---------------------------------------------------------------------------
# Integración con el loop
# ---------------------------------------------------------------------------


class TestLoopIntegration:
    @pytest.mark.asyncio
    async def test_turn_persists_history(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        store = HistoryStore(path=tmp_path / "h.json")
        loop, _, _ = make_loop(
            tmp_path, [call("read_file", path="a.py"), say("leído")], history_store=store
        )
        await loop.execute_turn("hola mundo")
        restored = store.load()
        assert [m.role for m in restored] == ["user", "assistant", "tool", "assistant"]
        assert restored[0].content == "hola mundo"

    @pytest.mark.asyncio
    async def test_reset_clears_history(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        loop, _, _ = make_loop(tmp_path, [say("ok")], history_store=store)
        await loop.execute_turn("algo")
        assert store.load()
        loop._reset_history()
        assert loop.history == []
        assert store.load() == []
