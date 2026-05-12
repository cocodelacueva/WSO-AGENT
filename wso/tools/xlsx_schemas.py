"""Schemas Pydantic para las tools de XLSX.

Definen la estructura declarativa que reciben `generate_xlsx` y derivadas
en su arg `sheets_json`. El modelo emite un JSON con una lista de sheets;
cada sheet tiene un nombre, headers opcionales, y filas de celdas.

Tipos de celda soportados
-------------------------

    str    : texto literal
    int    : número entero
    float  : número decimal
    bool   : true / false (se renderiza como TRUE/FALSE en Excel)
    None   : celda vacía
    "=..." : fórmula de Excel (string que empieza con "="). Se almacena
             literal en la celda y Excel la evalúa al abrir.

Estos tipos vienen "gratis" del decoder JSON: JSON.loads ya los
distingue. Pydantic 2 con smart union resolution los mapea sin coerción
agresiva.

Decisiones
----------

    - **Sin formato visual en v0.2.** Bold, colores, anchos de columna,
      validaciones, etc. quedan fuera para mantener la superficie chica.
      Si lo necesitamos, podemos agregar un arg opcional `header_style`
      o una tool aparte `format_xlsx_cell` en v0.3.

    - **Headers como row 1.** Si la sheet declara `headers`, esos
      strings se escriben en la primera fila con bold. Las `rows`
      empiezan en row 2. Si no hay headers, las rows empiezan en row 1.

    - **Filas heterogéneas permitidas.** Cada row es `list[CellValue]`
      independiente; no obligamos a que todas tengan el mismo ancho.
      Excel maneja filas de distinto largo sin problema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Tipos de celda. El orden importa para la smart-union de Pydantic: bool
# antes que int (porque bool es subclase de int en Python), int antes
# que float, etc. En la práctica, Pydantic v2 usa smart mode que elige
# el match más específico, pero declaramos el orden explícito por claridad.
CellValue = bool | int | float | str | None


class Sheet(BaseModel):
    """Una hoja del workbook.

    Si `headers` está vacío, las filas empiezan en row 1. Si hay headers,
    se escriben con bold en row 1 y las filas empiezan en row 2.
    """

    name: str = "Sheet1"
    headers: list[str] = Field(default_factory=list)
    rows: list[list[CellValue]] = Field(default_factory=list)


class Workbook(BaseModel):
    """Container del workbook completo (lista de sheets)."""

    sheets: list[Sheet]
