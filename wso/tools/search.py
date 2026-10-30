"""Tools de búsqueda: glob (archivos por nombre) y grep (contenido).

Son la forma barata de orientarse en un proyecto: en vez de leer archivos
enteros para encontrar algo, el modelo busca primero y lee solo lo que
importa. Las salidas están acotadas y usan paths relativos al proyecto.

`grep` usa ripgrep (`rg`) si está instalado (rápido, respeta .gitignore);
si no, cae a una búsqueda en Python.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from wso.tools.base import PermissionCategory, tool, truncate_with_notice
from wso.tools.filesystem import display_path, resolve_path

MAX_GLOB_RESULTS = 100
MAX_GREP_LINES = 100
MAX_GREP_CHARS = 8000

# Carpetas que nunca vale la pena recorrer.
IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", ".next", ".tox", "target", ".idea", ".vscode",
    }
)


def _is_ignored(p: Path, root: Path) -> bool:
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        parts = p.parts
    return any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in parts[:-1])


@tool(
    name="glob",
    category=PermissionCategory.READ,
    description=(
        "Busca archivos por patrón de nombre (ej: '*.py', 'src/**/*.ts', "
        "'test_*.py'). Un patrón sin '/' busca en todas las subcarpetas. "
        "Devuelve paths ordenados por modificación (más recientes primero)."
    ),
    args_schema={
        "pattern": "patrón glob",
        "path": "carpeta donde buscar (default: el proyecto)",
    },
)
def glob(pattern: str, path: str = ".") -> str:
    root = resolve_path(path)
    if not root.is_dir():
        raise NotADirectoryError(f"{display_path(root)} no es una carpeta.")
    pattern = pattern.strip().removeprefix("./") or "*"
    if "/" not in pattern:
        pattern = "**/" + pattern
    if pattern.endswith("**"):
        pattern += "/*"  # `dir/**` solo matchea carpetas en pathlib

    found = [p for p in root.glob(pattern) if not _is_ignored(p, root)]
    matches = [p for p in found if p.is_file()]
    if not matches:
        # El patrón nombra una carpeta (ej: "arbolito"): listar su contenido.
        matches = [
            f for d in found if d.is_dir()
            for f in d.rglob("*") if f.is_file() and not _is_ignored(f, root)
        ]
    if not matches:
        return f"Sin resultados para {pattern!r} en {display_path(root)}."
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    shown = [display_path(p) for p in matches[:MAX_GLOB_RESULTS]]
    if len(matches) > MAX_GLOB_RESULTS:
        shown.append(f"[… {len(matches) - MAX_GLOB_RESULTS} más. Usá un patrón más específico.]")
    return "\n".join(shown)


@tool(
    name="grep",
    category=PermissionCategory.READ,
    description=(
        "Busca una expresión regular en el contenido de los archivos. Devuelve "
        "líneas 'archivo:línea:texto'. Usalo para encontrar definiciones, usos "
        "o textos antes de leer archivos enteros."
    ),
    args_schema={
        "pattern": "expresión regular (ej: 'def load_', 'class \\w+Error')",
        "path": "archivo o carpeta donde buscar (default: el proyecto)",
        "include": "filtrar archivos por glob (ej: '*.py'); vacío = todos",
        "ignore_case": "true para ignorar mayúsculas/minúsculas",
    },
    aliases={"glob": "include", "regex": "pattern", "query": "pattern"},
)
def grep(pattern: str, path: str = ".", include: str = "", ignore_case: bool = False) -> str:
    root = resolve_path(path)
    if not root.exists():
        raise FileNotFoundError(f"No existe: {display_path(root)}")
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Regex inválida {pattern!r}: {e}") from e

    rg = shutil.which("rg")
    lines = (
        _grep_rg(rg, pattern, root, include, ignore_case)
        if rg
        else _grep_python(pattern, root, include, ignore_case)
    )
    if not lines:
        if include:
            return (
                f"Sin coincidencias para {pattern!r} en archivos {include!r}. "
                "Probá sin el filtro include (o con la extensión correcta)."
            )
        return f"Sin coincidencias para {pattern!r}."
    total = len(lines)
    lines = lines[:MAX_GREP_LINES]
    if total > MAX_GREP_LINES:
        lines.append(f"[… {total - MAX_GREP_LINES} coincidencias más. Acotá el patrón o el path.]")
    return truncate_with_notice("\n".join(lines), MAX_GREP_CHARS, what="los resultados")


def _grep_rg(rg: str, pattern: str, root: Path, include: str, ignore_case: bool) -> list[str]:
    cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
    if ignore_case:
        cmd.append("--ignore-case")
    if include:
        cmd += ["--glob", include]
    # rg ya respeta .gitignore; esto cubre proyectos sin git o sin ignore.
    for d in sorted(IGNORED_DIRS):
        cmd += ["--glob", f"!{d}/"]
    cmd += ["--regexp", pattern, str(root)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode not in (0, 1):  # 1 = sin coincidencias
        raise RuntimeError(f"rg falló: {proc.stderr.strip()}")
    cwd = str(Path.cwd().resolve()) + "/"
    return [line.removeprefix(cwd) for line in proc.stdout.splitlines()]


def _grep_python(pattern: str, root: Path, include: str, ignore_case: bool) -> list[str]:
    regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob(include or "*") if p.is_file() and not _is_ignored(p, root)
    )
    out: list[str] = []
    for f in files:
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                out.append(f"{display_path(f)}:{n}:{line[:300]}")
                if len(out) > MAX_GREP_LINES:
                    return out
    return out
