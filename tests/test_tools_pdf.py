"""Tests de la tool read_pdf.

Si `pypdf` no está instalado, el módulo se skipea con un mensaje claro.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip todo el módulo si pypdf no está disponible
pypdf = pytest.importorskip("pypdf")

from wso.tools.pdf import read_pdf  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_pdf(path: Path, pages_text: list[str]) -> Path:
    """Construir un PDF de texto plano con pypdf, una página por string."""
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter()
    for text in pages_text:
        page = writer.add_blank_page(width=612, height=792)
        # Para tests, no necesitamos que el texto sea extraíble realmente;
        # algunos casos lo verifican en archivos generados con texto real.
        # Pero para verificar el path/iteración, blank pages alcanzan.
        _ = page, RectangleObject  # silencia linters
        _ = text  # solo cuenta cantidad de páginas
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _make_pdf_with_text(path: Path, pages: list[str]) -> Path:
    """Construir un PDF con texto realmente extraíble usando reportlab si está."""
    try:
        from reportlab.pdfgen.canvas import Canvas
    except ImportError:
        pytest.skip("reportlab no instalado; no se puede generar PDF con texto")

    c = Canvas(str(path))
    for text in pages:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReadPdfPathValidation:
    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            read_pdf("relative/file.pdf")

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_pdf(str(tmp_path / "no_existe.pdf"))

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_pdf(str(tmp_path))


class TestReadPdfBlankPages:
    """PDFs con páginas en blanco (sin texto) — verifica el flujo."""

    def test_returns_string_with_path_header(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "blank.pdf"
        _make_simple_pdf(pdf_path, ["", ""])

        result = read_pdf(str(pdf_path))
        assert "Archivo:" in result
        assert "Páginas: 2" in result
        assert "--- Página 1 ---" in result
        assert "--- Página 2 ---" in result

    def test_warns_when_no_text_extractable(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "blank.pdf"
        _make_simple_pdf(pdf_path, ["", "", ""])

        result = read_pdf(str(pdf_path))
        # Cuando ninguna página tiene texto, agregamos el warning de OCR
        assert "OCR" in result or "imagen" in result


class TestReadPdfWithText:
    """PDFs con texto real, generados con reportlab si está disponible."""

    def test_extracts_text_from_pages(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "with_text.pdf"
        _make_pdf_with_text(
            pdf_path,
            ["Primera página de texto", "Segunda página distinta"],
        )

        result = read_pdf(str(pdf_path))
        assert "Primera página de texto" in result
        assert "Segunda página distinta" in result

    def test_preserves_page_order(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "ordered.pdf"
        _make_pdf_with_text(pdf_path, ["alfa", "beta", "gamma"])

        result = read_pdf(str(pdf_path))
        idx_a = result.index("alfa")
        idx_b = result.index("beta")
        idx_g = result.index("gamma")
        assert idx_a < idx_b < idx_g

    def test_page_count_is_correct(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "three.pdf"
        _make_pdf_with_text(pdf_path, ["a", "b", "c"])

        result = read_pdf(str(pdf_path))
        assert "Páginas: 3" in result


class TestReadPdfCorruptOrInvalid:
    def test_non_pdf_file_raises_value_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "not_a_pdf.pdf"
        bad.write_text("this is not a PDF, just some text")

        with pytest.raises(ValueError, match="abrir"):
            read_pdf(str(bad))


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestReadPdfRegistration:
    def test_registered_in_builtin_tools(self) -> None:
        from wso.tools.registry import load_builtin_tools

        registry = load_builtin_tools()
        assert "read_pdf" in registry

        defn = registry.get("read_pdf")
        assert defn is not None
        assert defn.category.value == "read"

    def test_appears_in_prompt_section(self) -> None:
        from wso.tools.registry import load_builtin_tools

        registry = load_builtin_tools()
        section = registry.to_prompt_section()
        assert "## read_pdf" in section
