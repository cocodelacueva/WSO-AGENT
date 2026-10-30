"""Manager de permisos: decide si una tool call necesita aprobación.

El "proyecto" es el directorio donde corrés `wso` (cwd). Reglas:

    READ    → auto dentro del proyecto. Fuera, pide aprobación.
    WRITE   → pide aprobación (con diff), salvo regla always o sesión.
    EXECUTE → auto si el comando es de solo lectura (ls, git status, rg…).
              Si no, pide aprobación salvo regla always o sesión.

Granularidad de las aprobaciones
--------------------------------
    `s` (sesión):
        WRITE dentro del proyecto → todas las ediciones del proyecto quedan
            aprobadas por la sesión (el "modo aceptar ediciones" de Claude
            Code). Fuera del proyecto → solo ese archivo.
        EXECUTE → comandos con el mismo prefijo (ej: `pytest`, `git commit`).
        READ fuera del proyecto → esa carpeta.
    `a` (siempre, persiste en permissions.toml):
        WRITE/READ → patrón `<carpeta>/*`.
        EXECUTE → prefijo del comando.

Un prefijo de comando nunca aprueba comandos encadenados (`;`, `&&`, `|`,
redirecciones, sustitución): `pytest && rm -rf x` siempre pregunta.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from wso.config import Settings
from wso.tools.base import PermissionCategory, ToolDefinition

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
    """Glob para el arg `path`."""

    command_prefix: str | None = None
    """Prefijo de comando para `bash` (ej: 'pytest', 'git commit')."""

    def matches(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self.tool != tool_name:
            return False
        if self.command_prefix is not None:
            command = args.get("command")
            return isinstance(command, str) and command_matches_prefix(
                command, self.command_prefix
            )
        if self.path_pattern is not None:
            path_arg = args.get("path")
            if not isinstance(path_arg, str):
                return False
            return fnmatch(str(_resolve(path_arg)), self.path_pattern)
        return True


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


@dataclass
class PermissionManager:
    """Estado de permisos de la sesión + reglas persistentes."""

    settings: Settings
    project_root: Path = field(default_factory=Path.cwd)
    _always_allows: list[AlwaysAllowRule] = field(default_factory=list, init=False)
    _session_write_all: bool = field(default=False, init=False)
    _session_paths: set[tuple[str, str]] = field(default_factory=set, init=False)
    """(tool, path resuelto) aprobados por sesión fuera del proyecto."""
    _session_read_dirs: list[Path] = field(default_factory=list, init=False)
    _session_prefixes: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.project_root = _resolve(str(self.project_root))
        self._load_always_allows()

    # ---- API pública ----

    def check(self, tool: ToolDefinition, args: dict[str, Any]) -> PermissionDecision:
        """Evaluar una tool call contra las reglas vigentes."""
        if self._auto(tool, args) or any(
            rule.matches(tool.name, args) for rule in self._always_allows
        ):
            return PermissionDecision.AUTO_APPROVED
        return PermissionDecision.NEEDS_APPROVAL

    def remember_session(self, tool: ToolDefinition, args: dict[str, Any]) -> None:
        """Aprobar para el resto de la sesión (ver granularidad en el docstring)."""
        if tool.category == PermissionCategory.EXECUTE:
            command = args.get("command")
            if isinstance(command, str):
                self._session_prefixes.add(command_prefix(command))
            return

        path = _path_arg(args)
        if tool.category == PermissionCategory.READ:
            target = path if path.is_dir() else path.parent
            self._session_read_dirs.append(target)
        elif self._in_project(path):
            self._session_write_all = True
        else:
            self._session_paths.add((tool.name, str(path)))

    def remember_always(self, tool: ToolDefinition, args: dict[str, Any]) -> None:
        """Aprobar siempre, persistiendo en permissions.toml."""
        if tool.category == PermissionCategory.EXECUTE:
            command = args.get("command")
            rule = AlwaysAllowRule(
                tool=tool.name,
                command_prefix=command_prefix(command) if isinstance(command, str) else None,
            )
        else:
            path = _path_arg(args)
            folder = path if path.is_dir() else path.parent
            rule = AlwaysAllowRule(tool=tool.name, path_pattern=str(folder / "*"))

        if rule not in self._always_allows:
            self._always_allows.append(rule)
            self._save_always_allows()
        self.remember_session(tool, args)

    def session_scope_hint(self, tool: ToolDefinition, args: dict[str, Any]) -> str:
        """Texto para la opción `s` del prompt: qué queda aprobado."""
        if tool.category == PermissionCategory.EXECUTE:
            command = args.get("command")
            prefix = command_prefix(command) if isinstance(command, str) else "?"
            return f"comandos que empiecen con `{prefix}`"
        path = _path_arg(args)
        if tool.category == PermissionCategory.WRITE and self._in_project(path):
            return "todas las ediciones dentro del proyecto"
        return "este archivo/carpeta"

    @property
    def always_allow_rules(self) -> list[AlwaysAllowRule]:
        return list(self._always_allows)

    # ---- Reglas automáticas y de sesión ----

    def _auto(self, tool: ToolDefinition, args: dict[str, Any]) -> bool:
        if tool.category == PermissionCategory.EXECUTE:
            command = args.get("command")
            if not isinstance(command, str):
                return False
            return is_readonly_command(command, self.project_root) or any(
                command_matches_prefix(command, p) for p in self._session_prefixes
            )

        path = _path_arg(args)
        if tool.category == PermissionCategory.READ:
            return self._in_project(path) or any(
                _is_under(path, d) for d in self._session_read_dirs
            )
        # WRITE
        if self._session_write_all and self._in_project(path):
            return True
        return (tool.name, str(path)) in self._session_paths

    def _in_project(self, path: Path) -> bool:
        return _is_under(path, self.project_root)

    # ---- Persistencia ----

    def _load_always_allows(self) -> None:
        """Cargar reglas desde config/permissions.toml si existe."""
        path = self.settings.permissions_file
        if not path.exists():
            return
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:  # noqa: BLE001 — TOML corrupto: lo ignoramos
            return

        for entry in data.get("allow", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str):
                continue
            pattern = entry.get("path_pattern")
            prefix = entry.get("command_prefix")
            if pattern is not None and not isinstance(pattern, str):
                continue
            if prefix is not None and not isinstance(prefix, str):
                continue
            self._always_allows.append(
                AlwaysAllowRule(tool=entry["tool"], path_pattern=pattern, command_prefix=prefix)
            )

    def _save_always_allows(self) -> None:
        """Escribir las reglas a permissions.toml (TOML manual, sin dependencia extra)."""
        path = self.settings.permissions_file
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Generated by WSO. Editá con cuidado: cada entrada concede permiso permanente.",
            "",
        ]
        for rule in self._always_allows:
            lines.append("[[allow]]")
            lines.append(f'tool = "{_escape_toml(rule.tool)}"')
            if rule.path_pattern is not None:
                lines.append(f'path_pattern = "{_escape_toml(rule.path_pattern)}"')
            if rule.command_prefix is not None:
                lines.append(f'command_prefix = "{_escape_toml(rule.command_prefix)}"')
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Clasificación de comandos
# ---------------------------------------------------------------------------

# Encadenar, redirigir o sustituir hace que un comando "inocente" pueda
# hacer cualquier cosa. Si aparece algo de esto, no hay auto-aprobación
# por prefijo.
_SHELL_METACHARS = (";", "&", "|", ">", "<", "`", "$(", "\n")

_READONLY_COMMANDS = frozenset(
    {
        "ls", "pwd", "cat", "head", "tail", "wc", "file", "which", "tree",
        "stat", "du", "df", "date", "whoami", "uname", "rg", "grep", "diff",
        "echo",
    }
)
_READONLY_GIT = frozenset({"status", "diff", "log", "show", "blame", "rev-parse"})
_FIND_UNSAFE = ("-exec", "-execdir", "-delete", "-ok", "-fprint", "-fls")

# Herramientas donde el subcomando importa: `git commit` ≠ `git push`.
_MULTIPLEXERS = frozenset(
    {"git", "npm", "pnpm", "yarn", "uv", "cargo", "go", "pip", "poetry", "docker", "make"}
)


def _split(command: str) -> list[str] | None:
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _is_simple(command: str) -> bool:
    return not any(m in command for m in _SHELL_METACHARS)


def is_readonly_command(command: str, project_root: Path | None = None) -> bool:
    """True si el comando solo lee (se puede auto-aprobar).

    Acepta pipes entre comandos de solo lectura (`git log | head`), pero
    nunca redirecciones, encadenamiento ni sustitución. Con `project_root`,
    además exige que los paths explícitos (`/x`, `~/x`, `../x`) estén
    dentro del proyecto: `cat ~/.ssh/id_rsa` no se auto-aprueba.
    """
    if any(m in command for m in (";", "&", ">", "<", "`", "$(", "\n")):
        return False
    segments = command.split("|")
    for segment in segments:
        tokens = _split(segment.strip())
        if not tokens:
            return False
        name = tokens[0]
        if name == "git":
            if len(tokens) < 2 or tokens[1] not in _READONLY_GIT:
                return False
        elif name == "find":
            if any(t in _FIND_UNSAFE for t in tokens):
                return False
        elif name not in _READONLY_COMMANDS:
            return False
        if project_root is not None and not all(
            _is_under(_resolve(t), project_root)
            for t in tokens[1:]
            if t.startswith(("/", "~")) or ".." in t
        ):
            return False
    return True


def command_prefix(command: str) -> str:
    """Prefijo que identifica 'el mismo tipo de comando'.

    `pytest -x tests/` → `pytest`; `git commit -m x` → `git commit`;
    `python -m pytest -q` → `python -m pytest`.
    """
    tokens = _split(command.strip()) or command.split()
    if not tokens:
        return ""
    if tokens[0] in ("python", "python3") and len(tokens) >= 3 and tokens[1] == "-m":
        return " ".join(tokens[:3])
    if tokens[0] in _MULTIPLEXERS and len(tokens) >= 2:
        return " ".join(tokens[:2])
    return tokens[0]


def command_matches_prefix(command: str, prefix: str) -> bool:
    """True si el comando es simple y sus primeros tokens son el prefijo."""
    if not prefix or not _is_simple(command):
        return False
    tokens = _split(command.strip())
    wanted = prefix.split()
    return tokens is not None and tokens[: len(wanted)] == wanted


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p


def _path_arg(args: dict[str, Any]) -> Path:
    path = args.get("path")
    return _resolve(path if isinstance(path, str) and path.strip() else ".")


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _escape_toml(value: str) -> str:
    """Escape mínimo para strings TOML básicos."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
