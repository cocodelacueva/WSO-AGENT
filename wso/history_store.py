"""Persistencia del historial de conversación entre sesiones.

Una sesión de `wso` arranca, por default, sin memoria de la anterior. Con
`WSO_HISTORY_PERSIST=true`, el `HistoryStore` guarda el historial (mensajes
de rol user/assistant) tras cada turno y lo restaura al arrancar, dando
continuidad.

Diseño (espeja a SessionLogger)
-------------------------------
    - **No-op por default.** Si `path is None`, `load()` devuelve `[]` y
      `save()` no escribe nada. Tests y runs sin persistencia no tocan disco.

    - **Solo user/assistant.** El system prompt NO se persiste: se reconstruye
      en cada arranque desde las tools y el contexto, así que guardarlo sería
      stale. El `AgentLoop.history` ya excluye el system message (se antepone
      al llamar al modelo), pero filtramos por las dudas.

    - **Escritura atómica.** Se escribe a un `.tmp` y se hace `replace()`, así
      una interrupción a mitad de guardado no deja el archivo corrupto.

    - **Cap de mensajes.** Se conservan los últimos `max_messages` para acotar
      el archivo y que el contexto no crezca sin límite entre sesiones.

    - **Tolerante a errores / corrupción.** Si el archivo está corrupto o
      falla la I/O, `load()` devuelve `[]` y `save()` se deshabilita en vez de
      tumbar la sesión.

Formato del archivo
-------------------
    {
      "saved_at": "2026-06-08T12:00:00.000Z",
      "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone  # noqa: UP017 — timezone.utc por compat amplia
from pathlib import Path

from wso.agent.model.base import Message

_PERSISTED_ROLES = ("user", "assistant")


def _utc_iso_now() -> str:
    """Timestamp UTC en ISO 8601 con sufijo Z (mismo formato que SessionLogger)."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


@dataclass
class HistoryStore:
    """Guarda y restaura el historial de conversación.

    Atributos:
        path: Archivo donde persistir. Si es None, el store es no-op.
        max_messages: Tope de mensajes a guardar/restaurar (últimos N).
    """

    path: Path | None
    max_messages: int = 200
    _disabled_by_error: bool = field(default=False, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        """True si el store va a leer/escribir."""
        return self.path is not None and not self._disabled_by_error

    # ---- API pública ----

    def load(self) -> list[Message]:
        """Cargar el historial guardado. Devuelve [] si no hay o si falla."""
        if not self.enabled:
            return []
        assert self.path is not None
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []  # corrupto: arrancamos limpio en vez de tumbar

        raw_messages = data.get("messages", []) if isinstance(data, dict) else []
        messages: list[Message] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in _PERSISTED_ROLES and isinstance(content, str):
                messages.append(Message(role=role, content=content))
        return messages[-self.max_messages :]

    def save(self, messages: list[Message]) -> None:
        """Persistir el historial (solo user/assistant, últimos max_messages)."""
        if not self.enabled:
            return
        assert self.path is not None

        kept = [m for m in messages if m.role in _PERSISTED_ROLES][-self.max_messages :]
        payload = {
            "saved_at": _utc_iso_now(),
            "messages": [{"role": m.role, "content": m.content} for m in kept],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
        except OSError:
            # No pudimos escribir: deshabilitamos para no spammear errores.
            self._disabled_by_error = True

    def clear(self) -> None:
        """Borrar el historial persistido (usado por el comando /reset)."""
        if self.path is None:
            return
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)
