"""Tests de la tool bash."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wso.tools import shell
from wso.tools.shell import bash, clip_output

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="comandos POSIX")


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


class TestBash:
    def test_runs_in_project_dir(self, tmp_path: Path) -> None:
        assert bash("pwd") == str(tmp_path.resolve())

    def test_reports_exit_code_and_stderr(self) -> None:
        out = bash("echo oops >&2; exit 3")
        assert out.startswith("oops")
        assert "exit code 3" in out

    def test_no_output(self) -> None:
        assert "sin salida" in bash("true")
        assert "exit code 1" in bash("false")

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError):
            bash("   ")

    def test_stdin_closed(self) -> None:
        # Un comando que espera input no cuelga: recibe EOF.
        assert "terminó bien" in bash("cat")

    def test_timeout_kills_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shell.settings, "bash_timeout", 1)
        out = bash("echo antes; sleep 5")
        assert out.startswith("[TIMEOUT tras 1s")
        assert "antes" in out

    def test_timeout_capped_by_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shell.settings, "bash_max_timeout", 1)
        assert bash("sleep 3", timeout_s=999).startswith("[TIMEOUT tras 1s")

    def test_output_clipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shell.settings, "bash_max_output_chars", 1000)
        out = bash("seq 1 5000")
        assert "omitidos" in out
        assert out.rstrip().endswith("5000")  # el final se conserva


class TestClipOutput:
    def test_short_unchanged(self) -> None:
        assert clip_output("abc", 100) == "abc"

    def test_keeps_head_and_tail(self) -> None:
        text = "H" * 5000 + "T" * 5000
        out = clip_output(text, 4000)
        assert out.startswith("H")
        assert out.endswith("T")
        assert "omitidos" in out
