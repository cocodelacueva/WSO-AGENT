"""Manager de permisos: decide si una tool call necesita aprobación.

Tres niveles de respuesta:
    - AUTO_APPROVED: la tool puede ejecutar sin preguntar
    - NEEDS_APPROVAL: hay que preguntar al usuario antes de ejecutar
    - DENIED: el usuario ya rechazó esto (sticky session/always)

Reglas v1:
    - Tools con categoría FLOW siempre AUTO_APPROVED
    - Tools READ con path dentro del whitelist → AUTO_APPROVED
    - Tools READ con path fuera del whitelist → NEEDS_APPROVAL (sticky por sesión)
    - Tools WRITE/DELETE/EXECUTE/NETWORK → NEEDS_APPROVAL siempre,
      salvo "always allow" guardado en permissions.toml

El whitelist inicial son las carpetas: /context, /tools, /output, cwd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from wso.config import Settings
from wso.tools.base import PermissionCategory, ToolDefinition


class PermissionDecision(Enum):
    AUTO_APPROVED = "auto_approved"
    NEEDS_APPROVAL = "needs_approval"
    DENIED = "denied"


@dataclass
class PermissionManager:
    """Estado de permisos de la sesión + reglas persistentes."""

    settings: Settings
    _read_whitelist: list[Path] = field(default_factory=list)
    """Carpetas auto-aprobadas para lectura."""

    _session_allows: set[tuple[str, str]] = field(default_factory=set)
    """Pares (tool_name, args_signature) aprobados solo para esta sesión."""

    _session_denies: set[tuple[str, str]] = field(default_factory=set)
    """Pares rechazados por la sesión (no volver a preguntar)."""

    _always_allows: set[tuple[str, str]] = field(default_factory=set)
    """Cargados desde permissions.toml. Persisten entre sesiones."""

    def __post_init__(self) -> None:
        # Whitelist inicial: workspace/context, automations, output + cwd
        self._read_whitelist = [
            self.settings.context_dir,
            self.settings.automations_dir,
            self.settings.output_dir,
            Path.cwd(),
        ]
        self._load_always_allows()

    def check(
        self,
        tool: ToolDefinition,
        args: dict[str, str],
    ) -> PermissionDecision:
        """Evaluar una tool call contra las reglas vigentes.

        TODO(v1): implementar lógica completa.
        """
        raise NotImplementedError("PermissionManager.check pendiente.")

    def remember_session(self, tool_name: str, args: dict[str, str]) -> None:
        """Marcar como aprobado para el resto de la sesión actual."""
        raise NotImplementedError

    def remember_always(self, tool_name: str, args: dict[str, str]) -> None:
        """Marcar como aprobado siempre. Persiste en permissions.toml."""
        raise NotImplementedError

    def _load_always_allows(self) -> None:
        """Cargar reglas persistentes desde config/permissions.toml.

        TODO(v1): implementar lectura de TOML.
        """
        pass
