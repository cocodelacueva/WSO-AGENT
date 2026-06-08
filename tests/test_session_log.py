"""Tests del SessionLogger (logging estructurado de sesiones a JSONL)."""

from __future__ import annotations

import json
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

import pytest

from wso.session_log import SessionLogger, _session_filename

# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------


class TestSessionFilename:
    def test_format(self) -> None:
        dt = datetime(2026, 5, 12, 14, 30, 22, tzinfo=timezone.utc)  # noqa: UP017
        assert _session_filename(dt) == "session_20260512T143022.jsonl"

    def test_pads_single_digits(self) -> None:
        dt = datetime(2026, 1, 1, 9, 5, 3, tzinfo=timezone.utc)  # noqa: UP017
        assert _session_filename(dt) == "session_20260101T090503.jsonl"


# ---------------------------------------------------------------------------
# Disabled (log_dir=None)
# ---------------------------------------------------------------------------


class TestDisabledLogger:
    def test_log_dir_none_disables(self) -> None:
        logger = SessionLogger(log_dir=None)
        assert logger.enabled is False
        assert logger.path is None

    def test_log_is_noop(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=None)
        logger.log("anything", x=1, y="z")
        # No files should exist
        assert list(tmp_path.iterdir()) == []
        assert logger.event_count == 0

    def test_close_is_safe(self) -> None:
        logger = SessionLogger(log_dir=None)
        logger.close()  # No debería tirar nada
        logger.close()  # Idempotente


# ---------------------------------------------------------------------------
# Enabled (log_dir=Path)
# ---------------------------------------------------------------------------


class TestEnabledLogger:
    def test_enabled_with_path(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        assert logger.enabled is True
        assert logger.path is not None
        assert logger.path.parent == tmp_path
        assert logger.path.name.startswith("session_")
        assert logger.path.suffix == ".jsonl"

    def test_lazy_file_open(self, tmp_path: Path) -> None:
        SessionLogger(log_dir=tmp_path)
        # Sin .log() calls, no debería crearse archivo
        assert list(tmp_path.iterdir()) == []

    def test_log_creates_file(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("test_event", value=42)
        logger.close()

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name.startswith("session_")
        assert files[0].suffix == ".jsonl"

    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("turn_start", input="hola")
        logger.log("tool_call", name="read_file", args={"path": "/x"})
        logger.close()

        content = logger.path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 2

        for line in lines:
            obj = json.loads(line)
            assert "t" in obj
            assert "event" in obj

    def test_includes_timestamp(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("ping")
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        # Timestamp ISO 8601 con sufijo Z
        assert obj["t"].endswith("Z")
        # Parseable
        # Acepta tanto "...123Z" como "...123456Z" — el formato exacto puede
        # variar levemente.
        datetime.strptime(obj["t"].rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")

    def test_event_count_increments(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        assert logger.event_count == 0
        logger.log("a")
        assert logger.event_count == 1
        logger.log("b")
        assert logger.event_count == 2
        logger.close()

    def test_session_id_is_stable(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        sid1 = logger.session_id
        logger.log("x")
        assert logger.session_id == sid1  # Mismo durante la sesión

    def test_multiple_events_same_file(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        for i in range(5):
            logger.log("event", i=i)
        logger.close()

        lines = logger.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            obj = json.loads(line)
            assert obj["i"] == i

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "subdir"
        # No existe todavía
        assert not nested.exists()

        logger = SessionLogger(log_dir=nested)
        logger.log("created")
        logger.close()

        assert nested.exists()
        files = list(nested.iterdir())
        assert len(files) == 1


# ---------------------------------------------------------------------------
# Encoding y caracteres especiales
# ---------------------------------------------------------------------------


class TestEncoding:
    def test_unicode_preserved(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("ping", text="café — niño — ¿qué tal?")
        logger.close()

        content = logger.path.read_text(encoding="utf-8")
        obj = json.loads(content.strip())
        assert obj["text"] == "café — niño — ¿qué tal?"

    def test_newlines_escaped(self, tmp_path: Path) -> None:
        """Newlines dentro de un evento deben quedar dentro del mismo line JSON."""
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("multi", text="línea 1\nlínea 2\nlínea 3")
        logger.close()

        content = logger.path.read_text(encoding="utf-8")
        # El archivo entero debería ser una sola línea (newlines escaped en JSON)
        assert content.count("\n") == 1  # solo el trailing
        obj = json.loads(content.strip())
        assert obj["text"] == "línea 1\nlínea 2\nlínea 3"

    def test_nested_dict_serialized(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("tool_call", args={"path": "/x", "nested": {"a": 1, "b": [2, 3]}})
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        assert obj["args"]["nested"]["b"] == [2, 3]

    def test_non_serializable_fallback_to_str(self, tmp_path: Path) -> None:
        class Weird:
            def __repr__(self) -> str:
                return "WeirdObject"

        logger = SessionLogger(log_dir=tmp_path)
        logger.log("test", obj=Weird())
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        assert obj["obj"] == "WeirdObject"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_with_statement(self, tmp_path: Path) -> None:
        path = None
        with SessionLogger(log_dir=tmp_path) as logger:
            logger.log("inside")
            path = logger.path

        # Después del `with`, el archivo está cerrado pero existe
        assert path is not None
        assert path.exists()

    def test_close_idempotent(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("x")
        logger.close()
        logger.close()  # No debería tirar


# ---------------------------------------------------------------------------
# Manejo de errores de I/O
# ---------------------------------------------------------------------------


class TestIOError:
    def test_log_dir_is_a_file_disables_logger(self, tmp_path: Path) -> None:
        # log_dir apunta a un archivo, no a un directorio
        not_a_dir = tmp_path / "i_am_a_file"
        not_a_dir.write_text("x")

        logger = SessionLogger(log_dir=not_a_dir)
        # No debería tirar — solo deshabilita el logger
        logger.log("ping")
        # _disabled_by_error debería estar en True después del fallo
        assert logger.enabled is False
        logger.close()


# ---------------------------------------------------------------------------
# Eventos esperados (smoke test del formato)
# ---------------------------------------------------------------------------


class TestEventShapes:
    def test_session_start_shape(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log(
            "session_start",
            model="qwen2.5-coder:14b",
            tools=["read_file", "write_file"],
            step_budget=10,
        )
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        assert obj["event"] == "session_start"
        assert obj["model"] == "qwen2.5-coder:14b"
        assert obj["tools"] == ["read_file", "write_file"]
        assert obj["step_budget"] == 10

    def test_tool_call_shape(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log("tool_call", name="read_file", args={"path": "/x"})
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        assert obj["event"] == "tool_call"
        assert obj["name"] == "read_file"
        assert obj["args"] == {"path": "/x"}

    def test_observation_shape(self, tmp_path: Path) -> None:
        logger = SessionLogger(log_dir=tmp_path)
        logger.log(
            "observation",
            tool="read_file",
            success=True,
            result="content",
            error=None,
        )
        logger.close()

        obj = json.loads(logger.path.read_text(encoding="utf-8").strip())
        assert obj["success"] is True
        assert obj["error"] is None


# ---------------------------------------------------------------------------
# Fixture compartido para tests que necesitan inspeccionar logs
# ---------------------------------------------------------------------------


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """Devuelve un directorio limpio para tests del logger."""
    d = tmp_path / "logs"
    d.mkdir()
    return d


def test_fixture_works(log_dir: Path) -> None:
    """Smoke test del fixture (sanity check)."""
    logger = SessionLogger(log_dir=log_dir)
    logger.log("ping")
    logger.close()
    assert any(log_dir.iterdir())
