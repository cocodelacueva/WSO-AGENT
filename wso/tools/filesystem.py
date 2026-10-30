"""Tools de archivos: read_file, write_file, edit_file.

Convenciones:
    - Los paths pueden ser relativos (se resuelven contra el directorio del
      proyecto, el cwd donde corrés `wso`) o absolutos. `~` se expande.
    - Las salidas están acotadas para no reventar el contexto de modelos
      locales: `read_file` devuelve un rango de líneas numeradas y avisa si
      el archivo sigue.
    - Errores se propagan como excepciones nativas (FileNotFoundError,
      ValueError, etc). El loop las captura y se las devuelve al modelo
      como resultado de error de la tool.
    - El gating de permisos NO vive acá (ver `permissions/manager.py`).
"""

from __future__ import annotations

from pathlib import Path

from wso.tools.base import PermissionCategory, tool

# Defaults pensados para contextos chicos (16k tokens): ~250 líneas de
# código son ~3k tokens. El modelo puede pedir más con offset/limit.
DEFAULT_READ_LINES = 250
MAX_READ_LINES = 1000
MAX_LINE_CHARS = 500


def resolve_path(path: str) -> Path:
    """Resolver un path del modelo: `~`, relativo al cwd, normalizado."""
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def display_path(p: Path) -> str:
    """Path relativo al proyecto si está adentro; absoluto si no."""
    try:
        return str(p.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def _read_text(p: Path) -> str:
    if not p.exists():
        raise FileNotFoundError(
            f"No existe: {display_path(p)}. Usá glob o grep para encontrar el archivo."
        )
    if p.is_dir():
        raise IsADirectoryError(
            f"{display_path(p)} es un directorio. Usá glob para listar su contenido."
        )
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        raise ValueError(f"{display_path(p)} parece binario; no se puede leer como texto.")
    return data.decode("utf-8", errors="replace")


@tool(
    name="read_file",
    category=PermissionCategory.READ,
    description=(
        "Lee un archivo de texto y devuelve sus líneas numeradas. Por default "
        f"devuelve las primeras {DEFAULT_READ_LINES} líneas; para archivos largos "
        "usá offset/limit para leer por tramos. Leé un archivo antes de editarlo."
    ),
    args_schema={
        "path": "ruta del archivo (relativa al proyecto o absoluta)",
        "offset": "número de la primera línea a leer (empieza en 1)",
        "limit": f"cantidad de líneas a leer (0 = {DEFAULT_READ_LINES}, máximo {MAX_READ_LINES})",
    },
    aliases={"file_path": "path", "filename": "path"},
)
def read_file(path: str, offset: int = 1, limit: int = 0) -> str:
    p = resolve_path(path)
    lines = _read_text(p).splitlines()
    total = len(lines)
    if total == 0:
        return f"({display_path(p)} está vacío)"

    start = max(offset, 1)
    if start > total:
        raise ValueError(f"offset={offset} supera el largo del archivo ({total} líneas).")
    count = DEFAULT_READ_LINES if limit <= 0 else min(limit, MAX_READ_LINES)
    end = min(start - 1 + count, total)

    out: list[str] = []
    for n in range(start, end + 1):
        line = lines[n - 1]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " …[línea recortada]"
        out.append(f"{n:>5}\t{line}")
    if end < total:
        out.append(
            f"[… mostradas líneas {start}-{end} de {total}. "
            f"Para seguir: read_file con offset={end + 1}]"
        )
    return "\n".join(out)


@tool(
    name="write_file",
    category=PermissionCategory.WRITE,
    description=(
        "Crea un archivo nuevo o reemplaza uno existente con el contenido dado. "
        "Crea las carpetas que falten. Para cambiar parte de un archivo "
        "existente preferí edit_file."
    ),
    args_schema={
        "path": "ruta del archivo (relativa al proyecto o absoluta)",
        "content": "contenido completo del archivo",
    },
    aliases={"file_path": "path", "filename": "path", "contents": "content"},
)
def write_file(path: str, content: str) -> str:
    p = resolve_path(path)
    if p.is_dir():
        raise IsADirectoryError(f"{display_path(p)} es un directorio.")
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    verb = "Sobrescrito" if existed else "Creado"
    return f"{verb} {display_path(p)} ({n_lines} líneas)."


def apply_edit(text: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Aplicar un reemplazo exacto y devolver el texto nuevo.

    Compartido por `edit_file` y por el preview (diff) del prompt de permisos.

    Raises:
        ValueError: si `old_string` no aparece, o aparece varias veces sin
            `replace_all`.
    """
    if old_string == new_string:
        raise ValueError("old_string y new_string son iguales: no hay nada que cambiar.")
    if not old_string:
        raise ValueError("old_string está vacío. Para crear un archivo usá write_file.")

    count = text.count(old_string)
    if count == 0:
        # Tolerancia: modelos chicos suelen agregar/quitar espacios o saltos
        # de línea en los bordes. Si la versión recortada es única, la usamos.
        stripped = old_string.strip()
        if stripped and text.count(stripped) == 1:
            return text.replace(stripped, new_string.strip(), 1)
        raise ValueError(
            "old_string no aparece en el archivo. Tiene que coincidir EXACTO "
            "(incluida la indentación). Releé el archivo con read_file y copiá "
            "el fragmento tal cual."
        )
    if count > 1 and not replace_all:
        raise ValueError(
            f"old_string aparece {count} veces. Agregá más líneas de contexto "
            "para que sea único, o usá replace_all=true."
        )
    return text.replace(old_string, new_string) if replace_all else text.replace(
        old_string, new_string, 1
    )


@tool(
    name="edit_file",
    category=PermissionCategory.WRITE,
    description=(
        "Edita un archivo reemplazando un fragmento exacto (old_string) por otro "
        "(new_string). old_string debe copiarse tal cual del archivo, con su "
        "indentación, e incluir suficientes líneas para ser único."
    ),
    args_schema={
        "path": "ruta del archivo (relativa al proyecto o absoluta)",
        "old_string": "fragmento exacto a reemplazar",
        "new_string": "texto nuevo que lo reemplaza",
        "replace_all": "true para reemplazar todas las apariciones",
    },
    aliases={"file_path": "path", "filename": "path", "old": "old_string", "new": "new_string"},
)
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    p = resolve_path(path)
    text = _read_text(p)
    new_text = apply_edit(text, old_string, new_string, replace_all)
    p.write_text(new_text, encoding="utf-8")
    return f"Editado {display_path(p)}.\n{_snippet_around(new_text, new_string)}"


def _snippet_around(text: str, needle: str, context: int = 3) -> str:
    """Unas líneas numeradas alrededor del cambio, para que el modelo confirme."""
    idx = text.find(needle.strip()) if needle.strip() else -1
    if idx < 0:
        return ""
    lines = text.splitlines()
    first = text.count("\n", 0, idx)
    last = first + needle.strip().count("\n")
    lo, hi = max(first - context, 0), min(last + context, len(lines) - 1)
    return "\n".join(f"{n + 1:>5}\t{lines[n]}" for n in range(lo, hi + 1))
