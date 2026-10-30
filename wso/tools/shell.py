"""Tool de shell: bash.

Corre un comando en el directorio del proyecto y devuelve exit code +
salida combinada. Es la tool para todo lo que no es leer/escribir
archivos: correr tests, linters, git, instalar dependencias, etc.

Contención:
    - Gate de permiso EXECUTE: pide aprobación salvo comandos de solo
      lectura (ver `permissions/manager.py`). Ese es el control real.
    - Timeout de pared con kill del grupo de procesos (no quedan hijos
      colgados).
    - stdin cerrado y pagers deshabilitados: un comando interactivo falla
      rápido en vez de colgar el loop.
    - Salida recortada conservando el principio y el FINAL (donde suelen
      estar los errores y el resumen de los tests).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from wso.config import settings
from wso.tools.base import PermissionCategory, tool

_HEAD_CHARS = 2000

# Procesos en curso, para poder matarlos si el usuario interrumpe (Ctrl+C):
# corren en su propia sesión, así que no reciben el SIGINT de la terminal.
_RUNNING: set[subprocess.Popen[str]] = set()


def kill_running() -> None:
    """Matar los comandos de bash en curso (lo llama el loop al interrumpir)."""
    posix = not sys.platform.startswith("win")
    for proc in list(_RUNNING):
        _kill(proc, posix)


@tool(
    name="bash",
    category=PermissionCategory.EXECUTE,
    description=(
        "Ejecuta un comando de shell en la carpeta del proyecto (tests, linters, "
        "git, instalar dependencias, etc). Devuelve el exit code y la salida. "
        "No uses comandos interactivos. Para leer, buscar o editar archivos "
        "usá read_file, grep, glob y edit_file en vez de cat/sed/find."
    ),
    args_schema={
        "command": "comando a ejecutar (ej: 'pytest -x -q', 'git status')",
        "timeout_s": "timeout en segundos (0 = default de la config)",
    },
    aliases={"cmd": "command"},
)
def bash(command: str, timeout_s: int = 0) -> str:
    if not command.strip():
        raise ValueError("El comando está vacío.")
    cap = settings.bash_max_timeout
    timeout = settings.bash_timeout if timeout_s <= 0 else min(timeout_s, cap)

    env = {**os.environ, "PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb"}
    posix = not sys.platform.startswith("win")
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=Path.cwd(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=posix,
    )
    _RUNNING.add(proc)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc, posix)
        output, _ = proc.communicate()
        header = f"[TIMEOUT tras {timeout}s: el proceso fue terminado]"
        return header + "\n" + clip_output(output or "", settings.bash_max_output_chars)
    finally:
        _RUNNING.discard(proc)

    elapsed = time.monotonic() - start
    header = f"[exit {proc.returncode}, {elapsed:.1f}s]"
    body = output.rstrip("\n") if output and output.strip() else "(sin salida)"
    return header + "\n" + clip_output(body, settings.bash_max_output_chars)


def _kill(proc: subprocess.Popen[str], posix: bool) -> None:
    try:
        if posix:
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover — Windows
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def clip_output(text: str, max_chars: int) -> str:
    """Recortar conservando el principio y el final (los errores suelen estar al final)."""
    if len(text) <= max_chars:
        return text
    head_len = min(_HEAD_CHARS, max_chars // 4)
    head = text[:head_len]
    tail = text[-(max_chars - head_len) :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n[… {omitted} caracteres omitidos …]\n{tail}"
