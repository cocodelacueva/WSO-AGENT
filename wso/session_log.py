"""Logging estructurado de sesiones de WSO en formato JSONL.

Una sesión = una corrida del REPL. Cada evento del agente (turn_start,
tool_call, permission_decision, observation, error, etc.) se serializa
como un objeto JSON en una sola línea, escrito al archivo
`{log_dir}/session_{timestamp}.jsonl`.

Diseño
------

    - **No-op por default.** Si `log_dir is None`, todas las llamadas a
      `.log()` son silenciosas. Esto permite que los tests y los runs
      sin logging configurado no toquen el filesystem.

    - **Lazy file open.** El archivo se crea recién al primer evento,
      así no quedan sesiones-fantasma vacías si el usuario abre y
      cierra `wso` sin tipear nada.

    - **One event per line.** Formato JSONL canónico: un objeto JSON
      independiente por línea, terminado en `\\n`. Permite parsear
      con `for line in f: json.loads(line)` y truncar/rotar sin parser.

    - **Timestamps en UTC.** Formato ISO 8601 con sufijo `Z`. El campo
      `t` está al inicio de cada evento (más fácil de leer / sortear).

    - **Tolerante a errores de I/O.** Si escribir falla (disco lleno,
      permisos), capturamos la excepción y mantenemos `enabled=False`
      para no spammear errores. El logging es opcional, no debería
      tumbar la sesión.

    - **Sin dependencias externas.** Solo stdlib (json, datetime, pathlib).

Formato de un archivo
---------------------

Cada línea es un objeto JSON con al menos `t` (timestamp ISO 8601 UTC)
y `event` (nombre del tipo de evento). Los campos extra dependen del
evento. Ejemplos abreviados:

    {"t": "...", "event": "session_start", "model": "...", "tools": [...], "step_budget": 10}
    {"t": "...", "event": "turn_start", "turn": 1, "input": "..."}
    {"t": "...", "event": "model_response", "text": "..."}
    {"t": "...", "event": "tool_call", "name": "read_file", "args": {...}}
    {"t": "...", "event": "permission", "tool": "...", "decision": "auto_approved"}
    {"t": "...", "event": "observation", "tool": "...", "success": true, "result": "..."}
    {"t": "...", "event": "turn_end", "turn": 1, "steps": 3, "duration_ms": 1611}
    {"t": "...", "event": "session_end", "turns": 4, "duration_ms": 278880}
    {"t": "...", "event": "error", "where": "...", "message": "...", "kind": "..."}
    {"t": "...", "event": "budget_continuation", "decision": "continue", "feedback": null}
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone  # noqa: UP017 — usar timezone.utc por compat amplia
from pathlib import Path
from typing import Any, TextIO


def _utc_iso_now() -> str:
    """Timestamp UTC con milisegundos, ISO 8601 con sufijo Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (  # noqa: UP017
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"  # noqa: UP017
    )


def _session_filename(started_at: datetime) -> str:
    """Nombre canónico para un archivo de sesión: session_YYYYMMDDTHHMMSS.jsonl."""
    return f"session_{started_at.strftime('%Y%m%dT%H%M%S')}.jsonl"


@dataclass
class SessionLogger:
    """Logger JSONL de eventos del agente.

    Uso típico:

        logger = SessionLogger(log_dir=Path("./logs"))   # habilitado
        logger.log("session_start", model="qwen2.5-coder:14b")
        ...
        logger.close()

        logger = SessionLogger(log_dir=None)             # no-op (default)
        logger.log("anything", x=1)                       # no escribe nada

    Atributos:
        log_dir: Directorio donde se crea el archivo .jsonl. Si es None,
            el logger es no-op (ideal para tests o runs sin logging).
        session_id: Identificador único de la sesión, derivado del
            timestamp de creación. Sirve para correlacionar eventos.
        path: Path completo al archivo .jsonl de esta sesión (resuelto
            cuando el logger está habilitado). None si está deshabilitado.
    """

    log_dir: Path | None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )
    _file: TextIO | None = field(default=None, init=False, repr=False)
    _disabled_by_error: bool = field(default=False, init=False, repr=False)
    _event_count: int = field(default=0, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        """True si el logger va a escribir eventos."""
        return self.log_dir is not None and not self._disabled_by_error

    @property
    def session_id(self) -> str:
        """ID legible de la sesión (timestamp ISO básico, útil para correlación)."""
        return self.started_at.strftime("%Y%m%dT%H%M%S")

    @property
    def path(self) -> Path | None:
        """Path al archivo de log, o None si está deshabilitado."""
        if self.log_dir is None:
            return None
        return self.log_dir / _session_filename(self.started_at)

    @property
    def event_count(self) -> int:
        """Cuántos eventos se escribieron en esta sesión."""
        return self._event_count

    # ---- API pública ----

    def log(self, event: str, **data: Any) -> None:
        """Escribir un evento al log.

        Args:
            event: Nombre del evento (ej: "turn_start", "tool_call", "error").
            **data: Datos extra del evento. Deben ser JSON-serializables.
                Si algo no es serializable, se castea a `str(value)` como
                fallback para no perder el evento.
        """
        if not self.enabled:
            return

        record = {"t": _utc_iso_now(), "event": event, **data}
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Fallback duro: si hasta default=str falla, perdemos el evento
            # antes que tumbar la sesión.
            return

        try:
            if self._file is None:
                self._open_file()
            assert self._file is not None  # narrowing
            self._file.write(line + "\n")
            self._file.flush()
            self._event_count += 1
        except OSError:
            # Disco lleno, permisos, lo que sea. Deshabilitamos para no
            # spammear errores en el resto de la sesión.
            self._disabled_by_error = True
            self._close_silently()

    def close(self) -> None:
        """Cerrar el archivo. Idempotente."""
        self._close_silently()

    # ---- Context manager ----

    def __enter__(self) -> SessionLogger:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ---- Internos ----

    def _open_file(self) -> None:
        """Crear el directorio y abrir el archivo en modo append."""
        assert self.log_dir is not None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / _session_filename(self.started_at)
        self._file = path.open("a", encoding="utf-8")

    def _close_silently(self) -> None:
        if self._file is not None:
            with contextlib.suppress(OSError):
                self._file.close()
            self._file = None
