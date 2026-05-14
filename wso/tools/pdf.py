"""Tool de PDF basada en pypdf.

Una sola tool de lectura:

    - read_pdf : extraer el texto plano de un .pdf existente, página por página.

Lazy import de `pypdf`: si la extra `[office]` no está instalada, la tool
falla con un mensaje guía solo cuando el modelo realmente la invoca.

Limitaciones conocidas:
    - PDFs escaneados (imágenes sin OCR) devolverán texto vacío o muy poco.
      El usuario tendrá que convertirlo con OCR aparte antes de usar la tool.
    - Tablas y layout complejo se aplanan a texto secuencial. Para parsing
      estructurado de tablas, considerar `pdfplumber` en una v2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wso.tools.base import PermissionCategory, tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_pypdf() -> Any:
    """Importar pypdf con un mensaje claro si falta la dep."""
    try:
        import pypdf  # noqa: PLC0415

        return pypdf
    except ImportError as e:  # pragma: no cover — depende del entorno
        raise ImportError(
            "pypdf no está instalado. Para usar read_pdf, "
            'instalá la extra: `pip install -e ".[office]"` '
            "o `pip install pypdf`."
        ) from e


def _validate_pdf_path_for_read(path: str) -> Path:
    """Validar que el path sea absoluto y apunte a un .pdf existente."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(
            f"La ruta debe ser absoluta: {path!r}. "
            f"Usá rutas tipo '/Users/...' o '~/...'."
        )
    if not p.exists():
        raise FileNotFoundError(f"El .pdf no existe: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio, no un archivo .pdf: {p}"
        )
    return p


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool(
    name="read_pdf",
    category=PermissionCategory.READ,
    description=(
        "Extrae el texto plano de un archivo .pdf existente, página por página. "
        "Devuelve un string estructurado con un header por página. "
        "Útil para resumir documentos, traducir, o tomar contenido de un deck "
        "exportado a PDF. NO funciona con PDFs escaneados sin OCR previo."
    ),
    args_schema={
        "path": "ruta absoluta del archivo .pdf a leer",
    },
)
def read_pdf(path: str) -> str:
    """Extraer texto de un .pdf, página por página."""
    pypdf = _require_pypdf()
    p = _validate_pdf_path_for_read(path)

    try:
        reader = pypdf.PdfReader(str(p))
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"No se pudo abrir {p} como PDF: {e}. "
            f"Puede estar corrupto o encriptado."
        ) from e

    if reader.is_encrypted:
        # pypdf puede intentar con password vacío
        try:
            reader.decrypt("")
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"PDF encriptado: {p}. "
                f"Necesita ser desencriptado antes de leerlo: {e}"
            ) from e

    num_pages = len(reader.pages)
    lines: list[str] = [f"Archivo: {p}", f"Páginas: {num_pages}", ""]

    total_chars = 0
    for idx, page in enumerate(reader.pages, start=1):
        lines.append(f"--- Página {idx} ---")
        try:
            text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001
            lines.append(f"(error extrayendo página: {e})")
            continue
        text = text.strip()
        if not text:
            lines.append("(página sin texto extraíble — puede ser imagen)")
        else:
            lines.append(text)
            total_chars += len(text)
        lines.append("")

    if total_chars == 0 and num_pages > 0:
        lines.append(
            "(ATENCIÓN: no se extrajo texto de ninguna página. "
            "El PDF podría ser escaneado y requerir OCR.)"
        )

    return "\n".join(lines)
