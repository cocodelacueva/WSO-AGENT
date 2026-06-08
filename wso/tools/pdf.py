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

# Tope default de caracteres por lectura. Acota cada read a un tamaño
# predecible para no desbordar el contexto del modelo (ver
# truncate_with_notice en base.py). El modelo puede subirlo si necesita más.
_DEFAULT_MAX_CHARS = 16000


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
        "max_chars": (
            "tope de caracteres a devolver (default 16000). Si el PDF es más "
            "largo, se corta en un límite de página y se avisa cuántas páginas "
            "quedaron sin incluir. Subilo si necesitás leer más de una vez."
        ),
        "start_page": (
            "primera página a incluir, base 1 (default 1). Útil para leer un "
            "PDF largo por tramos sin desbordar el contexto."
        ),
    },
)
def read_pdf(path: str, max_chars: int = _DEFAULT_MAX_CHARS, start_page: int = 1) -> str:
    """Extraer texto de un .pdf, página por página (con tope de tamaño)."""
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
    if start_page < 1:
        start_page = 1

    lines: list[str] = [f"Archivo: {p}", f"Páginas: {num_pages}", ""]

    total_chars = 0
    rendered_chars = 0
    stopped_at: int | None = None
    for idx in range(start_page, num_pages + 1):
        page = reader.pages[idx - 1]
        try:
            text = (page.extract_text() or "").strip()
        except Exception as e:  # noqa: BLE001
            lines.append(f"--- Página {idx} ---")
            lines.append(f"(error extrayendo página: {e})")
            lines.append("")
            continue

        # Cortar en un límite de página si ya pasamos el presupuesto.
        if max_chars and max_chars > 0 and rendered_chars >= max_chars and idx > start_page:
            stopped_at = idx
            break

        lines.append(f"--- Página {idx} ---")
        if not text:
            lines.append("(página sin texto extraíble — puede ser imagen)")
        else:
            lines.append(text)
            total_chars += len(text)
            rendered_chars += len(text)
        lines.append("")

    if stopped_at is not None:
        lines.append(
            f"[…TRUNCADO en la página {stopped_at}: el PDF tiene {num_pages} "
            f"páginas y se incluyeron {start_page}..{stopped_at - 1}. Para leer "
            f"el resto, volvé a llamar read_pdf con start_page={stopped_at} "
            f"(o subí max_chars). Trabajá con lo mostrado si ya alcanza.]"
        )

    if total_chars == 0 and num_pages > 0 and stopped_at is None:
        lines.append(
            "(ATENCIÓN: no se extrajo texto de ninguna página. "
            "El PDF podría ser escaneado y requerir OCR.)"
        )

    return "\n".join(lines)
