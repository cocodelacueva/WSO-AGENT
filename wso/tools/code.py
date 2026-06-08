"""Tool de ejecución de código Python: run_python.

Escape hatch del patrón híbrido (decisión 3.1 del DESIGN): para tareas raras
que no justifican una tool tipada propia, el modelo escribe Python y se
ejecuta en un subprocess contenido.

Modelo de contención
--------------------
El threat model es "el modelo emite código con bugs o raro", NO un adversario
intentando escapar activamente. Bajo ese modelo, la contención es:

    1. Subprocess separado (`sys.executable -I`), aislado del entorno e
       imports de usuario para que las corridas sean predecibles.
    2. cwd fijado a `workspace/run/` — las rutas relativas y los archivos
       temporales que cree el código caen ahí, no mezclados con el proyecto.
    3. Timeout de pared (`subprocess.run(timeout=...)`): mata corridas que
       cuelgan. Es el guard principal.
    4. Resource limits vía `resource.setrlimit` en el hijo (Unix):
       RLIMIT_CPU (segundos de CPU) y RLIMIT_FSIZE (tamaño de archivo). En
       Linux además RLIMIT_AS (memoria). En macOS RLIMIT_AS se omite porque
       es poco confiable y puede romper el arranque del intérprete.
    5. Red deshabilitada best-effort: un preámbulo neutraliza `socket`. NO es
       una jaula de red dura (código malicioso podría sortearlo), pero corta
       el acceso accidental/casual a la red, consistente con el threat model.
    6. Gate de permiso EXECUTE: cada corrida pide aprobación del usuario
       (no hay auto-aprobación para EXECUTE). Ese es el control real.

Esto NO es una jaula de filesystem: el subprocess puede leer/escribir donde
el usuario pueda (igual que cualquier proceso suyo). La contención apunta a
predecibilidad y a frenar accidentes, con la aprobación humana como gate.

Salida
------
Devuelve stdout + stderr combinados, con un header de exit code y duración.
Se trunca a `settings.run_python_max_output_chars` con aviso.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from wso.config import settings
from wso.tools.base import PermissionCategory, tool, truncate_with_notice

# Preámbulo que se antepone al código del usuario: deshabilita red
# (best-effort). Clave: NO reemplazamos `socket.socket` por una función
# (eso rompería `ssl`/`http`/`urllib`, que hacen `class SSLSocket(socket)`).
# En cambio subclaseamos socket y bloqueamos en `connect`, y neutralizamos
# create_connection/getaddrinfo. Así los módulos IMPORTAN bien; solo falla
# el uso real de red.
_NO_NETWORK_PREAMBLE = (
    "import socket as _wso_socket\n"
    "_wso_orig_socket = _wso_socket.socket\n"
    "def _wso_blocked(*a, **k):\n"
    "    raise RuntimeError('Red deshabilitada en run_python (sin acceso a red).')\n"
    "class _WsoNoNetSocket(_wso_orig_socket):\n"
    "    def connect(self, *a, **k):\n"
    "        _wso_blocked()\n"
    "    def connect_ex(self, *a, **k):\n"
    "        _wso_blocked()\n"
    "_wso_socket.socket = _WsoNoNetSocket\n"
    "_wso_socket.create_connection = _wso_blocked\n"
    "_wso_socket.getaddrinfo = _wso_blocked\n"
)


def _sandbox_dir() -> Path:
    """cwd del sandbox, creándolo si no existe. Extraído para testeabilidad."""
    d = settings.run_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_preexec():
    """Devolver un preexec_fn que aplica resource limits, o None si no aplica.

    Solo en Unix (en Windows `preexec_fn` y `resource` no existen).
    """
    if sys.platform.startswith("win"):
        return None
    try:
        import resource  # noqa: PLC0415 — solo Unix
    except ImportError:  # pragma: no cover
        return None

    cpu_seconds = settings.run_python_timeout + 1
    max_file_bytes = 64 * 1024 * 1024  # 64 MB por archivo escrito
    max_mem_bytes = settings.run_python_max_memory_mb * 1024 * 1024
    apply_mem = sys.platform.startswith("linux")  # RLIMIT_AS confiable solo en Linux

    def _preexec() -> None:  # pragma: no cover — corre en el subprocess hijo
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
        if apply_mem:
            resource.setrlimit(resource.RLIMIT_AS, (max_mem_bytes, max_mem_bytes))

    return _preexec


@tool(
    name="run_python",
    category=PermissionCategory.EXECUTE,
    description=(
        "Ejecuta código Python en un subprocess contenido (escape hatch para "
        "tareas sin una tool dedicada). Aislado, con timeout y sin acceso a "
        "red. cwd = workspace/run. Devuelve stdout+stderr. Imprimí con print() "
        "lo que quieras ver: el valor de la última expresión NO se muestra "
        "solo. Requiere aprobación del usuario en cada corrida."
    ),
    args_schema={
        "code": "código Python a ejecutar (usá print() para producir salida)",
        "timeout_s": "timeout en segundos (default y tope = config; ej 30)",
    },
)
def run_python(code: str, timeout_s: int = 0) -> str:
    """Ejecutar código Python en un subprocess contenido."""
    if not code.strip():
        raise ValueError("El código está vacío.")

    cap = settings.run_python_timeout
    # timeout_s=0 (o fuera de rango) → default de config. Tope = config.
    effective_timeout = cap if timeout_s <= 0 else min(timeout_s, cap)

    sandbox = _sandbox_dir()
    full_source = _NO_NETWORK_PREAMBLE + "\n" + code

    # Escribimos el código a un archivo temporal dentro del sandbox para que
    # los tracebacks tengan un nombre de archivo real.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", dir=sandbox, delete=False, encoding="utf-8"
    ) as tf:
        tf.write(full_source)
        script_path = Path(tf.name)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(script_path)],
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            preexec_fn=_build_preexec(),  # noqa: PLW1509 — intencional, Unix only
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - start
        partial = _decode(e.stdout) + _decode(e.stderr)
        body = (
            f"[run_python] TIMEOUT tras {effective_timeout}s "
            f"(corrió {elapsed:.1f}s). El proceso fue terminado.\n"
        )
        if partial.strip():
            body += "Salida parcial:\n" + partial
        return truncate_with_notice(
            body, settings.run_python_max_output_chars, what="la salida"
        )
    finally:
        # cleanup best-effort: no romper la corrida por un temp colgado.
        with contextlib.suppress(OSError):
            script_path.unlink(missing_ok=True)

    elapsed = time.monotonic() - start
    return _format_result(proc.returncode, proc.stdout, proc.stderr, elapsed)


def _format_result(returncode: int, stdout: str, stderr: str, elapsed: float) -> str:
    """Componer la salida legible para el modelo."""
    header = f"[run_python] exit={returncode}, {elapsed:.2f}s"
    parts = [header]
    if stdout.strip():
        parts.append("--- stdout ---\n" + stdout.rstrip("\n"))
    if stderr.strip():
        parts.append("--- stderr ---\n" + stderr.rstrip("\n"))
    if not stdout.strip() and not stderr.strip():
        parts.append("(sin salida; recordá usar print() para mostrar resultados)")
    body = "\n".join(parts)
    return truncate_with_notice(
        body, settings.run_python_max_output_chars, what="la salida"
    )


def _decode(value: object) -> str:
    """Normalizar stdout/stderr de TimeoutExpired (puede ser str, bytes o None)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
