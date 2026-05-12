"""Tools de Excel (.xlsx) basadas en openpyxl.

Cuatro tools declarativas:

    - generate_xlsx     : crear un .xlsx desde una estructura JSON
    - read_xlsx         : extraer datos (todas las sheets) como string
    - edit_xlsx_cell    : modificar una celda puntual (notación A1)
    - append_xlsx_rows  : agregar filas al final de una sheet

Convención del JSON de sheets
-----------------------------

Mismo workaround que pptx (JSON-in-string, ver decisión 3.15 en DESIGN.md):

    {
      "sheets": [
        {
          "name": "Presupuesto",
          "headers": ["Item", "Cantidad", "Precio", "Subtotal"],
          "rows": [
            ["Diseño",   10, 50,   "=B2*C2"],
            ["Edición",   5, 80,   "=B3*C3"],
            [null, null, "Total", "=SUM(D2:D3)"]
          ]
        },
        { "name": "Clientes", "rows": [["Acme", "acme@x.com"]] }
      ]
    }

También acepta una lista top-level como atajo cuando hay una sola sheet:

    [["Header1", "Header2"], [1, 2], [3, 4]]

Tipos de celda
--------------

JSON.loads ya devuelve `str | int | float | bool | None`. Cualquier
string que empiece con `=` se trata como fórmula de Excel y se almacena
literal — Excel la evalúa al abrir.

Errores
-------

    - ValueError              : path relativo / cell mal formado / json no es objeto/lista
    - FileNotFoundError       : el archivo no existe
    - IsADirectoryError       : el path apunta a un directorio
    - KeyError                : la sheet no existe (edit/append)
    - json.JSONDecodeError    : sheets_json / value_json / rows_json inválido
    - pydantic.ValidationError: la estructura no matchea Sheet/Workbook
    - ImportError             : openpyxl no está instalado (mensaje guía)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from wso.tools.base import PermissionCategory, tool
from wso.tools.xlsx_schemas import CellValue, Sheet, Workbook

# ---------------------------------------------------------------------------
# Lazy import de openpyxl
# ---------------------------------------------------------------------------


def _require_openpyxl() -> Any:
    """Importar openpyxl con un mensaje claro si falta la dep."""
    try:
        import openpyxl  # noqa: PLC0415

        return openpyxl
    except ImportError as e:  # pragma: no cover — depende del entorno
        raise ImportError(
            "openpyxl no está instalado. Para usar las tools de XLSX, "
            "instalá la extra: `pip install -e \".[office]\"` "
            "o `pip install openpyxl`."
        ) from e


# ---------------------------------------------------------------------------
# Helpers de path
# ---------------------------------------------------------------------------


def _validate_absolute(path: str, label: str = "ruta") -> Path:
    """Expandir `~` y verificar que el path sea absoluto."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(
            f"La {label} debe ser absoluta: {path!r}. "
            f"Usá rutas tipo '/Users/...' o '~/...'."
        )
    return p


def _validate_xlsx_path_for_read(path: str) -> Path:
    """Validar un path existente que apunta a un .xlsx legible."""
    p = _validate_absolute(path)
    if not p.exists():
        raise FileNotFoundError(f"El .xlsx no existe: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"La ruta es un directorio, no un archivo .xlsx: {p}"
        )
    return p


# ---------------------------------------------------------------------------
# Helpers de celdas y sheets
# ---------------------------------------------------------------------------


_CELL_REF_RE = re.compile(r"^[A-Za-z]{1,3}\d+$")


def _validate_cell_ref(cell: str) -> str:
    """Validar que `cell` sea una referencia A1 válida (ej: A1, AA10, ZZZ999).

    Devuelve la versión normalizada (uppercase).
    """
    if not _CELL_REF_RE.match(cell):
        raise ValueError(
            f"Referencia de celda inválida: {cell!r}. "
            f"Usá notación A1 (ej: 'A1', 'B12', 'AA100')."
        )
    return cell.upper()


def _write_row(ws: Any, row_idx: int, values: list[CellValue]) -> None:
    """Escribir una fila completa en la sheet.

    row_idx es 1-indexed (convención de openpyxl).
    """
    for col_idx, value in enumerate(values, start=1):
        ws.cell(row=row_idx, column=col_idx, value=value)


def _apply_sheet(wb: Any, sheet_data: Sheet, is_first: bool) -> Any:
    """Crear o reutilizar la sheet activa y poblarla con headers + rows.

    Si es la primera sheet, reutilizamos la default `wb.active` (creada
    automáticamente por openpyxl al hacer `Workbook()`). Para sheets
    siguientes, llamamos a `wb.create_sheet`.

    Aplica bold a los headers si están presentes.
    """
    openpyxl = _require_openpyxl()
    Font = openpyxl.styles.Font  # noqa: N806

    if is_first:
        ws = wb.active
        ws.title = sheet_data.name
    else:
        ws = wb.create_sheet(title=sheet_data.name)

    bold_font = Font(bold=True)
    next_row = 1

    if sheet_data.headers:
        for col_idx, header in enumerate(sheet_data.headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = bold_font
        next_row = 2

    for row_values in sheet_data.rows:
        _write_row(ws, next_row, row_values)
        next_row += 1

    return ws


def _parse_sheets_json(sheets_json: str) -> Workbook:
    """Parsear y validar sheets_json contra el schema Workbook.

    Acepta:
        - Un objeto {"sheets": [...]}.
        - Una lista de sheets [{...}, {...}].
        - Una lista de filas [[...], [...]] como atajo de "una sola sheet
          sin nombre ni headers" (útil para casos simples).

    Errores: json.JSONDecodeError / ValueError / pydantic.ValidationError.
    """
    raw = json.loads(sheets_json)

    # Caso 1: objeto envuelto
    if isinstance(raw, dict) and "sheets" in raw:
        return Workbook.model_validate(raw)

    # Caso 2/3: lista
    if isinstance(raw, list):
        if not raw:
            raise ValueError("sheets_json es una lista vacía. Pasá al menos una sheet.")
        first = raw[0]
        # Lista de sheets
        if isinstance(first, dict):
            return Workbook(sheets=raw)
        # Lista de filas (atajo)
        if isinstance(first, list):
            return Workbook(sheets=[Sheet(rows=raw)])
        raise ValueError(
            f"sheets_json es lista pero los elementos no son sheets ni filas: "
            f"primer elemento de tipo {type(first).__name__}."
        )

    raise ValueError(
        'sheets_json debe ser un objeto {"sheets":[...]}, una lista de sheets, '
        f"o una lista de filas. Recibido: {type(raw).__name__}."
    )


# ---------------------------------------------------------------------------
# Tool: generate_xlsx
# ---------------------------------------------------------------------------


@tool(
    name="generate_xlsx",
    category=PermissionCategory.WRITE,
    description=(
        "Genera un archivo .xlsx desde una estructura JSON de sheets. "
        "Soporta múltiples sheets, headers en bold (auto), celdas con "
        "str/int/float/bool/null, y fórmulas de Excel (strings que empiezan con '='). "
        "También acepta una lista de filas como atajo para una sola sheet."
    ),
    args_schema={
        "output_path": "ruta absoluta donde escribir el .xlsx",
        "sheets_json": (
            'JSON: {"sheets":[{"name":"...","headers":[...],"rows":[[...]]}]} '
            'o lista de sheets, o lista de filas. '
            'Ej: {"sheets":[{"name":"Q1","headers":["item","precio"],"rows":[["x",10]]}]}'
        ),
    },
)
def generate_xlsx(output_path: str, sheets_json: str) -> str:
    """Crear un .xlsx nuevo desde la estructura declarativa."""
    openpyxl = _require_openpyxl()

    target = _validate_absolute(output_path, label="output_path")
    if target.is_dir():
        raise IsADirectoryError(
            f"output_path es un directorio: {target}. "
            f"Pasá una ruta a un archivo .xlsx."
        )

    workbook_data = _parse_sheets_json(sheets_json)
    if not workbook_data.sheets:
        raise ValueError("Necesitás al menos una sheet para generar un .xlsx.")

    target.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    for idx, sheet_data in enumerate(workbook_data.sheets):
        _apply_sheet(wb, sheet_data, is_first=(idx == 0))

    wb.save(str(target))

    sheet_names = ", ".join(s.name for s in workbook_data.sheets)
    return (
        f"Generado: {target} "
        f"({len(workbook_data.sheets)} sheet{'s' if len(workbook_data.sheets) != 1 else ''}: "
        f"{sheet_names})"
    )


# ---------------------------------------------------------------------------
# Tool: read_xlsx
# ---------------------------------------------------------------------------


@tool(
    name="read_xlsx",
    category=PermissionCategory.READ,
    description=(
        "Extrae los datos de un .xlsx existente como string estructurado. "
        "Por cada sheet, muestra el nombre y las filas (separadas por |). "
        "Si max_rows > 0, trunca cada sheet a esa cantidad de filas "
        "(útil para previsualizar archivos grandes)."
    ),
    args_schema={
        "path": "ruta absoluta del archivo .xlsx a leer",
        "max_rows": (
            "máximo de filas a mostrar por sheet (0 = sin límite). "
            "Útil para previsualizar archivos grandes."
        ),
    },
)
def read_xlsx(path: str, max_rows: int = 0) -> str:
    """Leer un .xlsx y devolver su contenido como string estructurado."""
    openpyxl = _require_openpyxl()

    if max_rows < 0:
        raise ValueError(f"max_rows no puede ser negativo: {max_rows}")

    p = _validate_xlsx_path_for_read(path)
    wb = openpyxl.load_workbook(str(p), data_only=False)

    lines: list[str] = [f"Archivo: {p}", f"Sheets: {len(wb.sheetnames)}", ""]
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        lines.append(f"=== Sheet: {sheet_name} ({ws.max_row} filas × {ws.max_column} columnas) ===")

        for shown, row in enumerate(ws.iter_rows(values_only=True)):
            if max_rows and shown >= max_rows:
                lines.append(
                    f"  ... ({ws.max_row - shown} filas más, truncado por max_rows)"
                )
                break
            cells = [_format_cell_for_display(c) for c in row]
            lines.append("  " + " | ".join(cells))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_cell_for_display(value: Any) -> str:
    """Renderizar una celda como string para el output de read_xlsx."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


# ---------------------------------------------------------------------------
# Tool: edit_xlsx_cell
# ---------------------------------------------------------------------------


@tool(
    name="edit_xlsx_cell",
    category=PermissionCategory.WRITE,
    description=(
        "Modifica el valor de una celda puntual en una sheet existente. "
        "Notación A1 para la celda (ej: 'B3'). El valor es JSON: "
        '"texto", 42, 3.14, true, null, o "=SUM(A1:A10)" para fórmulas. '
        "Preserva el resto del .xlsx."
    ),
    args_schema={
        "path": "ruta absoluta del .xlsx (se modifica in-place)",
        "sheet": "nombre exacto de la sheet a editar (case-sensitive)",
        "cell": "referencia A1 de la celda (ej: 'A1', 'B12', 'AA100')",
        "value_json": (
            "valor a escribir, como JSON. Ej: \"hola\", 42, 3.14, true, null, "
            "\"=A1+B1\". Los strings que empiezan con '=' se tratan como fórmulas."
        ),
    },
)
def edit_xlsx_cell(path: str, sheet: str, cell: str, value_json: str) -> str:
    """Modificar una celda puntual de un .xlsx."""
    openpyxl = _require_openpyxl()

    p = _validate_xlsx_path_for_read(path)
    cell_ref = _validate_cell_ref(cell)

    try:
        value = json.loads(value_json)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"value_json no es JSON válido: {e}. "
            f'Si querés escribir un string, envolvelo en comillas: "{value_json}"'
        ) from e

    # Validar el tipo de valor recibido
    if value is not None and not isinstance(value, (bool, int, float, str)):
        raise ValueError(
            f"value_json debe ser str/int/float/bool/null, "
            f"recibido: {type(value).__name__}"
        )

    wb = openpyxl.load_workbook(str(p))
    if sheet not in wb.sheetnames:
        raise KeyError(
            f"La sheet {sheet!r} no existe en {p}. "
            f"Sheets disponibles: {wb.sheetnames}"
        )

    ws = wb[sheet]
    ws[cell_ref] = value
    wb.save(str(p))

    return f"Celda {cell_ref} de sheet {sheet!r} actualizada en {p}"


# ---------------------------------------------------------------------------
# Tool: append_xlsx_rows
# ---------------------------------------------------------------------------


@tool(
    name="append_xlsx_rows",
    category=PermissionCategory.WRITE,
    description=(
        "Agrega una o más filas al final de una sheet existente. "
        "Útil para tracking incremental (presupuestos, timelines, listas de clientes). "
        "Las filas son JSON: [[val1, val2, ...], ...]. "
        "Preserva el resto del .xlsx."
    ),
    args_schema={
        "path": "ruta absoluta del .xlsx (se modifica in-place)",
        "sheet": "nombre exacto de la sheet donde agregar (case-sensitive)",
        "rows_json": (
            "JSON array de filas, donde cada fila es un array de celdas. "
            'Ej: [["Cliente Acme", 1500.50, "2026-05-12"], ["Beta SA", 2200, "2026-05-15"]]'
        ),
    },
)
def append_xlsx_rows(path: str, sheet: str, rows_json: str) -> str:
    """Agregar filas al final de una sheet."""
    openpyxl = _require_openpyxl()

    p = _validate_xlsx_path_for_read(path)

    try:
        rows = json.loads(rows_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"rows_json no es JSON válido: {e}") from e

    if not isinstance(rows, list):
        raise ValueError(
            f"rows_json debe ser un array JSON de filas. Recibido: {type(rows).__name__}"
        )
    if not rows:
        raise ValueError("rows_json es una lista vacía. Pasá al menos una fila.")
    for i, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError(
                f"Cada fila debe ser una lista. La fila {i} es: {type(row).__name__}"
            )
        for j, cell_val in enumerate(row):
            if cell_val is not None and not isinstance(cell_val, (bool, int, float, str)):
                raise ValueError(
                    f"Celda en fila {i}, col {j} tiene tipo no soportado: "
                    f"{type(cell_val).__name__}. Permitido: str/int/float/bool/null."
                )

    wb = openpyxl.load_workbook(str(p))
    if sheet not in wb.sheetnames:
        raise KeyError(
            f"La sheet {sheet!r} no existe en {p}. "
            f"Sheets disponibles: {wb.sheetnames}"
        )

    ws = wb[sheet]
    starting_row = ws.max_row + 1
    for row in rows:
        ws.append(row)
    wb.save(str(p))

    return (
        f"Agregadas {len(rows)} fila{'s' if len(rows) != 1 else ''} a sheet {sheet!r} "
        f"(desde row {starting_row}) en {p}"
    )
