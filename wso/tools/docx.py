"""Tool de Word (.docx) basada en python-docx.

Una sola tool de lectura:

    - read_docx : extraer texto de un .docx (párrafos, headings y tablas).

Lazy import de `docx`: si la extra `[office]` no está instalada, la tool
falla con un mensaje guía solo cuando el modelo realmente la invoca.

Limitaciones conocidas:
    - Imágenes y objetos embebidos se omiten.
    - Estilos se preservan parcialmente: marcamos headings con `## ` para
      que el texto resultante quede navegable en markdown.
    - Tablas se serializan como filas separadas por `|`. Para parsing
      estructurado, considerar pasos de post-procesamiento.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wso.tools.base import PermissionCategory, tool, truncate_with_notice

# Tope default de caracteres por lectura (ver truncate_with_notice en base.py).
_DEFAULT_MAX_CHARS = 16000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_docx() -> Any:
    """Importar python-docx con un mensaje claro si falta la dep."""
    try:
        import docx  # noqa: PLC0415

        return docx
    except ImportError as e:  # pragma: no cover — depende del entorno
        raise ImportError(
            "python-docx no está instalado. Para usar read_docx, "
            'instalá la extra: `pip install -e ".[office]"` '
            "o `pip install python-docx`."
        ) from e


def _validate_docx_path_for_read(path: str) -> Path:
    """Validar que el path sea absoluto y apunte a un .docx existente."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(
            f"La ruta debe ser absoluta: {path!r}. "
            f"Usá rutas tipo '/Users/...' o '~/...'."
        )
    if not p.exists():
        raise FileNotFoundError(f"El .docx no existe: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio, no un archivo .docx: {p}"
        )
    return p


def _heading_level(style_name: str | None) -> int:
    """Detectar nivel de heading desde el style name de Word.

    Devuelve 0 si no es heading. Heading 1 → 1, Heading 2 → 2, etc.
    """
    if not style_name:
        return 0
    name = style_name.lower()
    if not name.startswith("heading "):
        return 0
    try:
        return int(name.split(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool(
    name="read_docx",
    category=PermissionCategory.READ,
    description=(
        "Extrae el contenido textual de un archivo .docx existente: "
        "párrafos, headings (marcados con ## según nivel) y tablas "
        "(serializadas con `|` entre celdas). Devuelve un string en "
        "estilo markdown. Útil para resumir documentos, traducir, o "
        "tomar contenido para una presentación."
    ),
    args_schema={
        "path": "ruta absoluta del archivo .docx a leer",
        "max_chars": (
            "tope de caracteres a devolver (default 16000). Si el documento es "
            "más largo, se trunca y se avisa cuánto quedó afuera. Subilo si "
            "necesitás el documento completo."
        ),
    },
)
def read_docx(path: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Extraer texto de un .docx en formato markdown-ish (con tope de tamaño)."""
    docx_lib = _require_docx()
    p = _validate_docx_path_for_read(path)

    try:
        doc = docx_lib.Document(str(p))
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"No se pudo abrir {p} como DOCX: {e}. "
            f"Puede estar corrupto o no ser un archivo Word válido."
        ) from e

    lines: list[str] = [f"Archivo: {p}", ""]

    # Iteramos sobre el body en orden de aparición. python-docx no expone
    # un iterador unificado para párrafos + tablas, así que recorremos el
    # XML del body directamente para preservar el orden.
    body = doc.element.body
    paragraphs_iter = iter(doc.paragraphs)
    tables_iter = iter(doc.tables)

    for child in body.iterchildren():
        tag = child.tag.split("}", 1)[-1]  # remover namespace

        if tag == "p":
            try:
                para = next(paragraphs_iter)
            except StopIteration:
                continue
            text = para.text.strip()
            if not text:
                continue
            level = _heading_level(para.style.name if para.style else None)
            if level > 0:
                prefix = "#" * min(level, 6)
                lines.append(f"{prefix} {text}")
            else:
                lines.append(text)
            lines.append("")

        elif tag == "tbl":
            try:
                table = next(tables_iter)
            except StopIteration:
                continue
            lines.append("")
            for row in table.rows:
                cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

    # Strip trailing whitespace y normalizar dobles blanks
    text = "\n".join(lines).rstrip() + "\n"
    return truncate_with_notice(
        text,
        max_chars,
        what="el documento",
        more_hint="Para el resto, volvé a leer con max_chars mayor.",
    )
