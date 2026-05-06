"""Tools de filesystem: read_file, write_file, delete_file, list_directory.

Convenciones:
    - Todas reciben paths absolutos. Los relativos se rechazan con ValueError.
    - Errores se propagan como excepciones nativas de Python
      (FileNotFoundError, IsADirectoryError, etc). El loop las captura
      y las formatea como observación de error para el modelo.
    - `~` se expande automáticamente (`Path.expanduser`).
    - El gating de permisos NO vive acá. Estas funciones asumen que ya
      fueron aprobadas por `permissions/manager.py`.
"""

from __future__ import annotations

from pathlib import Path

from wso.tools.base import PermissionCategory, tool


def _validate_absolute(path: str) -> Path:
    """Expandir `~` y verificar que el path sea absoluto."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(
            f"La ruta debe ser absoluta: {path!r}. "
            f"Usá rutas tipo '/Users/...' o '~/...'."
        )
    return p


@tool(
    name="read_file",
    category=PermissionCategory.READ,
    description="Lee el contenido completo de un archivo de texto y lo devuelve como string.",
    args_schema={
        "path": "ruta absoluta del archivo a leer (ej: /Users/coco/notes.md)",
    },
)
def read_file(path: str) -> str:
    """Leer un archivo de texto."""
    p = _validate_absolute(path)
    if not p.exists():
        raise FileNotFoundError(f"El archivo no existe: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio, no un archivo: {p}. "
            f"Usá list_directory para listar su contenido."
        )
    return p.read_text(encoding="utf-8")


@tool(
    name="write_file",
    category=PermissionCategory.WRITE,
    description=(
        "Escribe contenido a un archivo. Sobreescribe si el archivo existe. "
        "Crea las carpetas padres si no existen."
    ),
    args_schema={
        "path": "ruta absoluta donde escribir el archivo",
        "content": "contenido completo a escribir (texto plano UTF-8)",
    },
)
def write_file(path: str, content: str) -> str:
    """Escribir contenido a un archivo (sobreescribe)."""
    p = _validate_absolute(path)
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio, no se puede escribir como archivo: {p}"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Escritos {len(content)} caracteres en {p}"


@tool(
    name="delete_file",
    category=PermissionCategory.DELETE,
    description=(
        "Elimina un archivo individual. NO funciona en directorios "
        "(usá una herramienta separada cuando exista)."
    ),
    args_schema={
        "path": "ruta absoluta del archivo a eliminar",
    },
)
def delete_file(path: str) -> str:
    """Eliminar un archivo."""
    p = _validate_absolute(path)
    if not p.exists():
        raise FileNotFoundError(f"No existe: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio: {p}. delete_file solo elimina archivos."
        )
    p.unlink()
    return f"Eliminado: {p}"


@tool(
    name="list_directory",
    category=PermissionCategory.READ,
    description=(
        "Lista archivos y subdirectorios de una carpeta. "
        "Marca [D] para directorios y [F] para archivos. "
        "Los directorios aparecen primero, luego archivos, ambos ordenados alfabéticamente."
    ),
    args_schema={
        "path": "ruta absoluta del directorio a listar",
    },
)
def list_directory(path: str) -> str:
    """Listar el contenido de un directorio."""
    p = _validate_absolute(path)
    if not p.exists():
        raise FileNotFoundError(f"El directorio no existe: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"La ruta no es un directorio: {p}")

    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    if not entries:
        return f"(directorio vacío: {p})"

    lines = [f"Contenido de {p}:"]
    for entry in entries:
        marker = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"  {marker} {entry.name}")
    return "\n".join(lines)
