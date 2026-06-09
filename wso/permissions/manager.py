"""Manager de permisos: decide si una tool call necesita aprobación.

Reglas v1
---------
    - Tools con categoría FLOW siempre AUTO_APPROVED.
    - Tools READ con path dentro del whitelist → AUTO_APPROVED.
    - Tools con regla "always allow" matcheando → AUTO_APPROVED.
    - Tools en session_allows → AUTO_APPROVED.
    - Cualquier otra cosa → NEEDS_APPROVAL.

Granularidad de las aprobaciones
--------------------------------
    Session-allows usa la firma exacta de args (predecible y conservador).
    Always-allows usa pattern matching sobre el path cuando la tool tiene
    un arg `path` (más útil porque cubre múltiples archivos del mismo dir).

Whitelist inicial de lectura
----------------------------
    workspace/context, workspace/automations, workspace/output, cwd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wso.config import Settings
from wso.tools.base import PermissionCategory, ToolDefinition

# tomllib (3.11+) con fallback a tomli (3.10 backport)
try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - solo Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Tipos públicos
# ---------------------------------------------------------------------------


class PermissionDecision(Enum):
    AUTO_APPROVED = "auto_approved"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class AlwaysAllowRule:
    """Regla 'always allow' persistente cargada desde permissions.toml."""

    tool: str
    """Nombre de la tool (matchea exactamente)."""

    path_pattern: str | None = None
    """Glob para el arg `path`. None = matchea cualquier args."""

    def matches(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self.tool != tool_name:
            return False
        if self.path_pattern is None:
            return True
        path_arg = args.get("path")
        if not isinstance(path_arg, str):
            return False
        # Normalizar el path antes de matchear (resolver `~` y rutas relativas)
        try:
            normalized = str(Path(path_arg).expanduser().resolve())
        except (OSError, RuntimeError):
            normalized = path_arg
        return fnmatch(normalized, self.path_pattern)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


@dataclass
class PermissionManager:
    """Estado de permisos de la sesión + reglas persistentes."""

    settings: Settings
    _read_whitelist: list[Path] = field(default_factory=list, init=False)
    _session_allows: set[tuple[str, frozenset[tuple[str, str]]]] = field(
        default_factory=set, init=False
    )
    _always_allows: list[AlwaysAllowRule] = field(default_factory=list, init=False)
    _browser_session_allows: set[tuple[str, str]] = field(
        default_factory=set, init=False
    )
    """Sticky de browser por sesión: pares (grupo, dominio). Las tools de
    navegación (open_tab, navigate) comparten el grupo 'navigate', así
    aprobar una cubre futuras navegaciones al mismo dominio. No persiste
    entre sesiones (decisión de diseño A.5: nada `always` para browser)."""

    def __post_init__(self) -> None:
        self._read_whitelist = [
            self._safe_resolve(self.settings.context_dir),
            self._safe_resolve(self.settings.automations_dir),
            self._safe_resolve(self.settings.output_dir),
            self._safe_resolve(Path.cwd()),
        ]
        self._load_always_allows()

    # ---- API pública ----

    def check(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
    ) -> PermissionDecision:
        """Evaluar una tool call contra las reglas vigentes."""
        # 1. FLOW siempre auto
        if tool.category == PermissionCategory.FLOW:
            return PermissionDecision.AUTO_APPROVED

        # 2. READ con path en whitelist → auto
        if tool.category == PermissionCategory.READ:
            path_arg = args.get("path")
            if isinstance(path_arg, str) and self._path_in_read_whitelist(path_arg):
                return PermissionDecision.AUTO_APPROVED

        # 2b. BROWSER con dominio en el sticky de sesión → auto
        if tool.category == PermissionCategory.BROWSER:
            domain = self._browser_domain(args)
            if domain is not None:
                group = self._browser_group(tool.name)
                if any(
                    g == group and self._domain_matches(domain, allowed)
                    for (g, allowed) in self._browser_session_allows
                ):
                    return PermissionDecision.AUTO_APPROVED

        # 3. Always-allows
        if any(rule.matches(tool.name, args) for rule in self._always_allows):
            return PermissionDecision.AUTO_APPROVED

        # 4. Session-allows (exact match)
        if (tool.name, self._args_signature(args)) in self._session_allows:
            return PermissionDecision.AUTO_APPROVED

        return PermissionDecision.NEEDS_APPROVAL

    def remember_session(self, tool_name: str, args: dict[str, Any]) -> None:
        """Aprobar esta tool call para el resto de la sesión (exact match)."""
        self._session_allows.add((tool_name, self._args_signature(args)))

    def remember_browser_session(self, tool_name: str, args: dict[str, Any]) -> None:
        """Aprobar una tool de browser para la sesión, sticky por dominio.

        Si la tool trae una `url` (navegación), recuerda el dominio para que
        futuras navegaciones del mismo grupo al mismo dominio (o subdominios)
        se auto-aprueben. Si no hay `url` (click, type, close_tab), cae al
        sticky genérico de sesión por args exactos.
        """
        domain = self._browser_domain(args)
        if domain is not None:
            self._browser_session_allows.add((self._browser_group(tool_name), domain))
        else:
            self.remember_session(tool_name, args)

    def remember_always(self, tool_name: str, args: dict[str, Any]) -> None:
        """Aprobar siempre, persistiendo en permissions.toml.

        Para tools con arg `path`, genera un patrón `<parent_dir>/*` que
        cubre todos los archivos del mismo directorio. Para otras tools,
        crea una regla sin pattern (cualquier args matchea).
        """
        path_arg = args.get("path")
        path_pattern: str | None = None
        if isinstance(path_arg, str):
            try:
                resolved = Path(path_arg).expanduser().resolve()
                path_pattern = str(resolved.parent / "*")
            except (OSError, RuntimeError):
                path_pattern = None

        rule = AlwaysAllowRule(tool=tool_name, path_pattern=path_pattern)

        # Evitar duplicados exactos
        if rule not in self._always_allows:
            self._always_allows.append(rule)
            self._save_always_allows()

        # También aplicar de inmediato a la sesión actual
        self.remember_session(tool_name, args)

    # ---- Introspección (útil para tests y debugging) ----

    @property
    def read_whitelist(self) -> list[Path]:
        return list(self._read_whitelist)

    @property
    def always_allow_rules(self) -> list[AlwaysAllowRule]:
        return list(self._always_allows)

    @property
    def browser_session_allows(self) -> set[tuple[str, str]]:
        return set(self._browser_session_allows)

    # ---- Helpers internos ----

    @staticmethod
    def _safe_resolve(path: Path) -> Path:
        """Resolver un path tolerando carpetas que aún no existen."""
        try:
            return path.expanduser().resolve()
        except (OSError, RuntimeError):
            return path

    def _path_in_read_whitelist(self, path_str: str) -> bool:
        """Chequear si el path está dentro de alguna carpeta del whitelist."""
        try:
            target = Path(path_str).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        return any(self._is_under(target, root) for root in self._read_whitelist)

    @staticmethod
    def _is_under(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _args_signature(args: dict[str, Any]) -> frozenset[tuple[str, str]]:
        """Firma estable de un dict de args, comparable entre llamadas."""
        return frozenset((k, str(v)) for k, v in args.items())

    # ---- Browser sticky ----

    # Las tools de navegación comparten grupo: aprobar la navegación a un
    # dominio cubre tanto open_tab como navigate a ese dominio.
    _BROWSER_NAV_TOOLS = frozenset({"browser_open_tab", "browser_navigate"})

    @classmethod
    def _browser_group(cls, tool_name: str) -> str:
        """Grupo de sticky para una tool de browser."""
        return "navigate" if tool_name in cls._BROWSER_NAV_TOOLS else tool_name

    @staticmethod
    def _browser_domain(args: dict[str, Any]) -> str | None:
        """Extraer el dominio normalizado (sin 'www.') del arg `url`, si hay."""
        url = args.get("url")
        if not isinstance(url, str):
            return None
        host = urlparse(url).hostname
        if not host:
            return None
        host = host.lower()
        return host[4:] if host.startswith("www.") else host

    @staticmethod
    def _domain_matches(target: str, allowed: str) -> bool:
        """True si `target` es `allowed` o un subdominio suyo."""
        return target == allowed or target.endswith("." + allowed)

    # ---- Persistencia ----

    def _load_always_allows(self) -> None:
        """Cargar reglas desde config/permissions.toml si existe."""
        path = self.settings.permissions_file
        if not path.exists():
            return
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            # TOML corrupto: ignoramos silenciosamente. En producción
            # esto debería loguear un warning.
            return

        for entry in data.get("allow", []):
            if not isinstance(entry, dict):
                continue
            tool = entry.get("tool")
            if not isinstance(tool, str):
                continue
            pattern = entry.get("path_pattern")
            if pattern is not None and not isinstance(pattern, str):
                continue
            self._always_allows.append(
                AlwaysAllowRule(tool=tool, path_pattern=pattern)
            )

    def _save_always_allows(self) -> None:
        """Escribir las reglas vigentes a permissions.toml.

        Escribimos TOML manualmente (formato simple, evita dependencia
        adicional de tomli_w).
        """
        path = self.settings.permissions_file
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "# Generated by WSO. Editá con cuidado: cada entrada concede "
            "permiso permanente.",
            "",
        ]
        for rule in self._always_allows:
            lines.append("[[allow]]")
            lines.append(f'tool = "{_escape_toml(rule.tool)}"')
            if rule.path_pattern is not None:
                lines.append(f'path_pattern = "{_escape_toml(rule.path_pattern)}"')
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


def _escape_toml(value: str) -> str:
    """Escape mínimo para strings TOML básicos (basic strings)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
