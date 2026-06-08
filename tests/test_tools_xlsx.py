"""Tests de las tools de XLSX.

Cubre:
    - Schemas Pydantic (Sheet, Workbook, tipado de celdas)
    - generate_xlsx: una sheet, múltiples sheets, headers en bold,
      fórmulas, atajo de lista de filas, errores
    - read_xlsx: extracción, multi-sheet, max_rows truncation
    - edit_xlsx_cell: modificación puntual, fórmulas, errores
    - append_xlsx_rows: append, preservación del resto, errores

Si openpyxl no está instalado, el módulo entero se skipea con
`pytest.importorskip("openpyxl")`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from pydantic import ValidationError  # noqa: E402

from wso.tools.xlsx import (  # noqa: E402
    append_xlsx_rows,
    edit_xlsx_cell,
    generate_xlsx,
    read_xlsx,
)
from wso.tools.xlsx_schemas import Sheet, Workbook  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_simple_wb(tmp_path: Path, sheets: list[dict] | dict | list) -> Path:
    """Helper: generar un .xlsx desde la estructura dada y devolver la ruta."""
    target = tmp_path / "wb.xlsx"
    generate_xlsx(str(target), json.dumps(sheets))
    return target


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_sheet_minimal(self) -> None:
        s = Sheet()
        assert s.name == "Sheet1"
        assert s.headers == []
        assert s.rows == []

    def test_sheet_with_data(self) -> None:
        s = Sheet(name="Q1", headers=["Item", "Precio"], rows=[["x", 10], ["y", 20.5]])
        assert s.headers == ["Item", "Precio"]
        assert s.rows[0] == ["x", 10]

    def test_workbook_validates_sheets(self) -> None:
        wb = Workbook(sheets=[Sheet(name="A"), Sheet(name="B")])
        assert len(wb.sheets) == 2

    def test_sheet_accepts_mixed_cell_types(self) -> None:
        s = Sheet(
            rows=[
                ["text", 42, 3.14, True, None, "=SUM(A1:A10)"],
            ]
        )
        row = s.rows[0]
        assert row[0] == "text"
        assert row[1] == 42
        assert row[2] == 3.14
        assert row[3] is True
        assert row[4] is None
        assert row[5] == "=SUM(A1:A10)"

    def test_workbook_rejects_unknown_field(self) -> None:
        # Pydantic acepta extras por default; este test documenta el comportamiento.
        # Si en el futuro queremos rechazar campos extras, agregar
        # `model_config = ConfigDict(extra="forbid")` a los schemas.
        wb = Workbook(sheets=[{"name": "X", "unknown_field": 1}])  # type: ignore[list-item]
        assert wb.sheets[0].name == "X"

    def test_workbook_rejects_non_list_rows(self) -> None:
        with pytest.raises(ValidationError):
            Sheet(rows="not a list")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# generate_xlsx
# ---------------------------------------------------------------------------


class TestGenerateXlsx:
    def test_minimal_workbook(self, tmp_path: Path) -> None:
        target = tmp_path / "min.xlsx"
        result = generate_xlsx(
            str(target),
            json.dumps({"sheets": [{"name": "Hola", "rows": [["a", 1]]}]}),
        )
        assert target.exists()
        assert "1 sheet" in result
        assert "Hola" in result

    def test_pluralizes_sheet_count(self, tmp_path: Path) -> None:
        target = tmp_path / "multi.xlsx"
        wb_data = {
            "sheets": [
                {"name": "Uno", "rows": [["x"]]},
                {"name": "Dos", "rows": [["y"]]},
                {"name": "Tres", "rows": [["z"]]},
            ]
        }
        result = generate_xlsx(str(target), json.dumps(wb_data))
        assert "3 sheets" in result

    def test_headers_applied_with_bold(self, tmp_path: Path) -> None:
        target = tmp_path / "head.xlsx"
        wb_data = {
            "sheets": [
                {
                    "name": "Costo",
                    "headers": ["Item", "Precio"],
                    "rows": [["pizza", 1500], ["coca", 800]],
                }
            ]
        }
        generate_xlsx(str(target), json.dumps(wb_data))

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Costo"]
        # Headers en row 1
        assert ws["A1"].value == "Item"
        assert ws["B1"].value == "Precio"
        # Bold aplicado
        assert ws["A1"].font.bold is True
        assert ws["B1"].font.bold is True
        # Data row no es bold
        assert ws["A2"].value == "pizza"
        assert ws["A2"].font.bold in (False, None)

    def test_rows_start_at_row_1_when_no_headers(self, tmp_path: Path) -> None:
        target = tmp_path / "nohead.xlsx"
        wb_data = {"sheets": [{"name": "X", "rows": [["a", 1], ["b", 2]]}]}
        generate_xlsx(str(target), json.dumps(wb_data))

        wb = openpyxl.load_workbook(str(target))
        ws = wb["X"]
        assert ws["A1"].value == "a"
        assert ws["B1"].value == 1
        assert ws["A2"].value == "b"

    def test_cell_types_preserved(self, tmp_path: Path) -> None:
        target = tmp_path / "types.xlsx"
        wb_data = {
            "sheets": [
                {
                    "rows": [
                        ["texto", 42, 3.14, True, False, None],
                    ]
                }
            ]
        }
        generate_xlsx(str(target), json.dumps(wb_data))

        wb = openpyxl.load_workbook(str(target))
        ws = wb.active
        assert ws["A1"].value == "texto"
        assert ws["B1"].value == 42
        assert isinstance(ws["B1"].value, int)
        assert ws["C1"].value == 3.14
        assert isinstance(ws["C1"].value, float)
        assert ws["D1"].value is True
        assert ws["E1"].value is False
        assert ws["F1"].value is None

    def test_formula_persisted_literally(self, tmp_path: Path) -> None:
        target = tmp_path / "formula.xlsx"
        wb_data = {
            "sheets": [
                {
                    "rows": [
                        [10, 20, "=A1+B1"],
                        [100, 200, "=SUM(A1:B2)"],
                    ]
                }
            ]
        }
        generate_xlsx(str(target), json.dumps(wb_data))

        wb = openpyxl.load_workbook(str(target), data_only=False)
        ws = wb.active
        # openpyxl preserva fórmulas como strings que empiezan con "="
        assert ws["C1"].value == "=A1+B1"
        assert ws["C2"].value == "=SUM(A1:B2)"

    def test_accepts_list_of_sheets_shortcut(self, tmp_path: Path) -> None:
        target = tmp_path / "listsheets.xlsx"
        # Lista de sheets directa (sin wrap en {"sheets": ...})
        data = [
            {"name": "A", "rows": [["x"]]},
            {"name": "B", "rows": [["y"]]},
        ]
        generate_xlsx(str(target), json.dumps(data))

        wb = openpyxl.load_workbook(str(target))
        assert wb.sheetnames == ["A", "B"]

    def test_accepts_list_of_rows_shortcut(self, tmp_path: Path) -> None:
        target = tmp_path / "rows_only.xlsx"
        # Atajo: lista de filas → una sola sheet con nombre default
        rows = [["Header1", "Header2"], [1, 2], [3, 4]]
        result = generate_xlsx(str(target), json.dumps(rows))
        assert "1 sheet" in result

        wb = openpyxl.load_workbook(str(target))
        ws = wb.active
        assert ws["A1"].value == "Header1"
        assert ws["A2"].value == 1
        assert ws["B3"].value == 4

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "deep.xlsx"
        generate_xlsx(str(target), json.dumps([[1, 2]]))
        assert target.exists()

    # ---- Errores ----

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            generate_xlsx("rel.xlsx", json.dumps([[1]]))

    def test_directory_as_output_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            generate_xlsx(str(tmp_path), json.dumps([[1]]))

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        with pytest.raises(json.JSONDecodeError):
            generate_xlsx(str(tmp_path / "x.xlsx"), "{ not valid")

    def test_empty_list_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="vacía"):
            generate_xlsx(str(tmp_path / "x.xlsx"), json.dumps([]))

    def test_empty_sheets_array_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="al menos una sheet"):
            generate_xlsx(str(tmp_path / "x.xlsx"), json.dumps({"sheets": []}))

    def test_non_object_non_list_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="objeto"):
            generate_xlsx(str(tmp_path / "x.xlsx"), json.dumps("string raro"))

    def test_invalid_sheet_shape_raises(self, tmp_path: Path) -> None:
        # Lista pero con primer elemento que no es dict ni list
        with pytest.raises(ValueError, match="sheets ni filas"):
            generate_xlsx(str(tmp_path / "x.xlsx"), json.dumps(["string", "raro"]))


# ---------------------------------------------------------------------------
# read_xlsx
# ---------------------------------------------------------------------------


class TestReadXlsx:
    def test_extracts_all_sheets(self, tmp_path: Path) -> None:
        target = _build_simple_wb(
            tmp_path,
            {
                "sheets": [
                    {"name": "Uno", "rows": [["a", 1]]},
                    {"name": "Dos", "rows": [["b", 2]]},
                ]
            },
        )
        result = read_xlsx(str(target))
        assert "Sheet: Uno" in result
        assert "Sheet: Dos" in result
        assert "Sheets: 2" in result
        assert "a" in result
        assert "1" in result

    def test_includes_headers_in_output(self, tmp_path: Path) -> None:
        target = _build_simple_wb(
            tmp_path,
            {
                "sheets": [
                    {
                        "name": "Costo",
                        "headers": ["Item", "Precio"],
                        "rows": [["pizza", 1500]],
                    }
                ]
            },
        )
        result = read_xlsx(str(target))
        assert "Item" in result
        assert "Precio" in result
        assert "pizza" in result

    def test_renders_booleans_explicitly(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [[True, False]])
        result = read_xlsx(str(target))
        assert "TRUE" in result
        assert "FALSE" in result

    def test_renders_nulls_as_empty(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x", None, "y"]])
        result = read_xlsx(str(target))
        # Verificamos que el output contiene la fila con un valor vacío en medio
        assert "x |  | y" in result

    def test_max_rows_truncates(self, tmp_path: Path) -> None:
        rows = [[f"row-{i}", i] for i in range(20)]
        target = _build_simple_wb(tmp_path, rows)
        result = read_xlsx(str(target), max_rows=5)
        assert "row-0" in result
        assert "row-4" in result
        assert "row-19" not in result
        assert "truncado" in result

    def test_max_rows_zero_means_no_limit(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["a"], ["b"], ["c"]])
        result = read_xlsx(str(target), max_rows=0)
        assert "a" in result
        assert "b" in result
        assert "c" in result
        assert "truncado" not in result

    def test_negative_max_rows_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["a"]])
        with pytest.raises(ValueError, match="negativo"):
            read_xlsx(str(target), max_rows=-1)

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            read_xlsx("rel.xlsx")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_xlsx(str(tmp_path / "nope.xlsx"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_xlsx(str(tmp_path))


# ---------------------------------------------------------------------------
# edit_xlsx_cell
# ---------------------------------------------------------------------------


class TestEditXlsxCell:
    def test_writes_string(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["original"]])
        edit_xlsx_cell(str(target), "Sheet1", "A1", json.dumps("modificado"))

        wb = openpyxl.load_workbook(str(target))
        assert wb["Sheet1"]["A1"].value == "modificado"

    def test_writes_number(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        edit_xlsx_cell(str(target), "Sheet1", "B1", json.dumps(42))

        wb = openpyxl.load_workbook(str(target))
        assert wb["Sheet1"]["B1"].value == 42

    def test_writes_formula(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [[10, 20]])
        edit_xlsx_cell(str(target), "Sheet1", "C1", json.dumps("=A1+B1"))

        wb = openpyxl.load_workbook(str(target), data_only=False)
        assert wb["Sheet1"]["C1"].value == "=A1+B1"

    def test_writes_null(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        edit_xlsx_cell(str(target), "Sheet1", "A1", json.dumps(None))

        wb = openpyxl.load_workbook(str(target))
        assert wb["Sheet1"]["A1"].value is None

    def test_writes_boolean(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        edit_xlsx_cell(str(target), "Sheet1", "B1", json.dumps(True))

        wb = openpyxl.load_workbook(str(target))
        assert wb["Sheet1"]["B1"].value is True

    def test_lowercase_cell_ref_normalized(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        # 'a1' debería aceptarse y normalizarse a 'A1'
        edit_xlsx_cell(str(target), "Sheet1", "a1", json.dumps("ok"))
        wb = openpyxl.load_workbook(str(target))
        assert wb["Sheet1"]["A1"].value == "ok"

    def test_preserves_other_cells(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["a", "b", "c"], ["d", "e", "f"]])
        edit_xlsx_cell(str(target), "Sheet1", "B1", json.dumps("MODIFICADO"))

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Sheet1"]
        assert ws["A1"].value == "a"
        assert ws["B1"].value == "MODIFICADO"
        assert ws["C1"].value == "c"
        assert ws["A2"].value == "d"
        assert ws["B2"].value == "e"
        assert ws["C2"].value == "f"

    # ---- Errores ----

    def test_invalid_cell_ref_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="Referencia"):
            edit_xlsx_cell(str(target), "Sheet1", "no-es-celda", json.dumps("x"))

    def test_unknown_sheet_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(KeyError, match="no existe"):
            edit_xlsx_cell(str(target), "NoExiste", "A1", json.dumps("x"))

    def test_invalid_json_value_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="JSON"):
            edit_xlsx_cell(str(target), "Sheet1", "A1", "no es json")

    def test_unsupported_value_type_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        # Una lista no es un valor de celda válido
        with pytest.raises(ValueError, match="str/int/float/bool/null"):
            edit_xlsx_cell(str(target), "Sheet1", "A1", json.dumps(["lista"]))

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            edit_xlsx_cell(
                str(tmp_path / "nope.xlsx"), "Sheet1", "A1", json.dumps("x")
            )


# ---------------------------------------------------------------------------
# append_xlsx_rows
# ---------------------------------------------------------------------------


class TestAppendXlsxRows:
    def test_appends_single_row(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["a", 1]])
        result = append_xlsx_rows(
            str(target), "Sheet1", json.dumps([["b", 2]])
        )
        assert "1 fila" in result

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Sheet1"]
        assert ws["A1"].value == "a"
        assert ws["A2"].value == "b"
        assert ws["B2"].value == 2

    def test_appends_multiple_rows(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["a"]])
        result = append_xlsx_rows(
            str(target), "Sheet1", json.dumps([["b"], ["c"], ["d"]])
        )
        assert "3 filas" in result

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Sheet1"]
        values = [ws.cell(row=i, column=1).value for i in range(1, 5)]
        assert values == ["a", "b", "c", "d"]

    def test_preserves_headers(self, tmp_path: Path) -> None:
        target = _build_simple_wb(
            tmp_path,
            {
                "sheets": [
                    {
                        "name": "Sheet1",
                        "headers": ["Cliente", "Monto"],
                        "rows": [["Acme", 1500]],
                    }
                ]
            },
        )
        append_xlsx_rows(
            str(target), "Sheet1", json.dumps([["Beta SA", 2200]])
        )

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Sheet1"]
        # Header sigue en row 1 con bold
        assert ws["A1"].value == "Cliente"
        assert ws["A1"].font.bold is True
        # Original row 2
        assert ws["A2"].value == "Acme"
        # Nuevo en row 3
        assert ws["A3"].value == "Beta SA"
        assert ws["B3"].value == 2200

    def test_supports_formulas_in_rows(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [[10, 20, "=A1+B1"]])
        append_xlsx_rows(
            str(target), "Sheet1", json.dumps([[30, 40, "=A2+B2"]])
        )

        wb = openpyxl.load_workbook(str(target), data_only=False)
        ws = wb["Sheet1"]
        assert ws["C2"].value == "=A2+B2"

    def test_supports_mixed_cell_types(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        append_xlsx_rows(
            str(target),
            "Sheet1",
            json.dumps([["text", 42, 3.14, True, None]]),
        )

        wb = openpyxl.load_workbook(str(target))
        ws = wb["Sheet1"]
        assert ws["A2"].value == "text"
        assert ws["B2"].value == 42
        assert ws["C2"].value == 3.14
        assert ws["D2"].value is True
        assert ws["E2"].value is None

    # ---- Errores ----

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="JSON"):
            append_xlsx_rows(str(target), "Sheet1", "{ not json")

    def test_non_list_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="array"):
            append_xlsx_rows(str(target), "Sheet1", json.dumps({"not": "list"}))

    def test_empty_rows_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="vacía"):
            append_xlsx_rows(str(target), "Sheet1", json.dumps([]))

    def test_row_not_list_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(ValueError, match="fila"):
            append_xlsx_rows(
                str(target), "Sheet1", json.dumps(["no soy lista"])
            )

    def test_invalid_cell_type_in_row_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        # Una lista anidada como celda no es válida
        with pytest.raises(ValueError, match="tipo no soportado"):
            append_xlsx_rows(
                str(target), "Sheet1", json.dumps([[{"objeto": "raro"}]])
            )

    def test_unknown_sheet_raises(self, tmp_path: Path) -> None:
        target = _build_simple_wb(tmp_path, [["x"]])
        with pytest.raises(KeyError, match="no existe"):
            append_xlsx_rows(
                str(target), "NoExiste", json.dumps([["x"]])
            )

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            append_xlsx_rows(
                str(tmp_path / "nope.xlsx"), "Sheet1", json.dumps([["x"]])
            )

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            append_xlsx_rows("rel.xlsx", "Sheet1", json.dumps([["x"]]))
