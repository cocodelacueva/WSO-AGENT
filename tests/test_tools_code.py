"""Tests de run_python (sandbox subprocess).

Corren subprocesses REALES (con sys.executable), pero el cwd del sandbox se
redirige a un tmp_path para ser herméticos y no depender de workspace/run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wso.tools import code
from wso.tools.base import PermissionCategory, get_tool_definition
from wso.tools.code import run_python


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path) -> Path:
    """Redirige el cwd del sandbox a un tmp_path hermético."""
    monkeypatch.setattr(code, "_sandbox_dir", lambda: tmp_path)
    return tmp_path


class TestMetadata:
    def test_category_is_execute(self) -> None:
        defn = get_tool_definition(run_python)
        assert defn is not None
        assert defn.category == PermissionCategory.EXECUTE


class TestBasicExecution:
    def test_prints_output(self, sandbox: Path) -> None:
        result = run_python("print(2 + 2)")
        assert "exit=0" in result
        assert "4" in result

    def test_stdlib_available(self, sandbox: Path) -> None:
        result = run_python("import json, math; print(json.dumps({'x': 1}))")
        assert '{"x": 1}' in result

    def test_no_output_hint(self, sandbox: Path) -> None:
        result = run_python("x = 5")
        assert "exit=0" in result
        assert "sin salida" in result

    def test_empty_code_raises(self, sandbox: Path) -> None:
        with pytest.raises(ValueError, match="vac"):
            run_python("   ")

    def test_cwd_is_sandbox(self, sandbox: Path) -> None:
        result = run_python("import os; print(os.getcwd())")
        assert str(sandbox) in result


class TestErrors:
    def test_exception_captured(self, sandbox: Path) -> None:
        result = run_python("x = 1 / 0")
        assert "exit=1" in result
        assert "ZeroDivisionError" in result
        assert "stderr" in result

    def test_syntax_error_captured(self, sandbox: Path) -> None:
        result = run_python("def broken(:\n  pass")
        assert "exit=1" in result
        assert "SyntaxError" in result


class TestNetworkBlocked:
    def test_socket_connect_blocked(self, sandbox: Path) -> None:
        result = run_python(
            "import socket\n"
            "s = socket.socket()\n"
            "s.connect(('1.1.1.1', 80))\n"
        )
        assert "exit=1" in result
        assert "Red deshabilitada" in result

    def test_imports_still_work(self, sandbox: Path) -> None:
        # ssl/http/urllib deben IMPORTAR bien aunque la red esté bloqueada.
        result = run_python(
            "import ssl, http.client, urllib.request\nprint('imports ok')"
        )
        assert "exit=0" in result
        assert "imports ok" in result


class TestTimeout:
    def test_timeout_kills_process(self, monkeypatch, sandbox: Path) -> None:
        monkeypatch.setattr(code.settings, "run_python_timeout", 1)
        result = run_python("while True:\n    pass")
        assert "TIMEOUT" in result

    def test_requested_timeout_capped_to_config(
        self, monkeypatch, sandbox: Path
    ) -> None:
        # Pide 9999s pero el tope de config es 1s → debe cortar a ~1s.
        monkeypatch.setattr(code.settings, "run_python_timeout", 1)
        result = run_python("while True:\n    pass", timeout_s=9999)
        assert "TIMEOUT" in result


class TestResourceLimits:
    @pytest.mark.skipif(sys.platform.startswith("win"), reason="resource es Unix-only")
    def test_preexec_builds_on_unix(self) -> None:
        fn = code._build_preexec()
        assert fn is not None
