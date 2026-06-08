"""Schemas Pydantic para las tools de PPTX.

Definen la estructura declarativa que recibe `generate_pptx` (y derivadas)
en su arg `slides_json`. El modelo emite un JSON array de objetos donde
cada objeto representa un slide; el campo `layout` discrimina la variante.

Layouts soportados (mapean a layouts default de python-pptx):

    title          → layout 0  (Title Slide: título + subtítulo)
    content        → layout 1  (Title + bullets)
    section_header → layout 2  (Section header — divisor de secciones)
    two_content    → layout 3  (Dos columnas de bullets)
    image          → layout 5  (Título + imagen)
    blank          → layout 6  (Blank — solo título opcional)

Decisiones de diseño:

    - Discriminated union por `layout`: Pydantic valida la forma exacta
      según el valor del campo discriminante. Mensajes de error claros.

    - `notes` opcional en todos los layouts: corresponde a speaker notes.
      Vacío por default.

    - `image.image_path` debe ser absoluto y local (no URLs remotas en v1).
      El gating por permisos es WRITE — pero la lectura del archivo de
      imagen NO pasa por el sistema de permisos. Asumimos que si el
      usuario aprobó la escritura del .pptx, también confía en las
      imágenes que pidió incluir.

    - `bullets` es `list[str]`: cada elemento es un bullet point.
      Si necesitás sub-bullets, los podés indentar con tabs/espacios
      en el string (python-pptx no maneja jerarquía automática vía API).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class _SlideBase(BaseModel):
    """Campos comunes a todos los layouts de slide."""

    notes: str = ""
    """Speaker notes (texto en el panel de notas del slide). Vacío = sin notas."""


class TitleSlide(_SlideBase):
    """Slide de portada / título principal."""

    layout: Literal["title"]
    title: str
    subtitle: str = ""


class ContentSlide(_SlideBase):
    """Slide estándar con título y bullets."""

    layout: Literal["content"]
    title: str
    bullets: list[str] = Field(default_factory=list)


class SectionHeaderSlide(_SlideBase):
    """Slide divisor de sección (típicamente con título grande)."""

    layout: Literal["section_header"]
    title: str
    subtitle: str = ""


class TwoContentSlide(_SlideBase):
    """Slide con dos columnas de bullets (comparativa, izquierda/derecha)."""

    layout: Literal["two_content"]
    title: str
    left_bullets: list[str] = Field(default_factory=list)
    right_bullets: list[str] = Field(default_factory=list)


class ImageSlide(_SlideBase):
    """Slide con título e imagen.

    `image_path` debe ser una ruta absoluta a un archivo de imagen local
    (.png, .jpg, .jpeg, .gif). No se soportan URLs remotas en v1.
    """

    layout: Literal["image"]
    title: str
    image_path: str
    caption: str = ""


class BlankSlide(_SlideBase):
    """Slide en blanco. Útil para casos custom o como placeholder."""

    layout: Literal["blank"]
    title: str = ""


Slide = Annotated[
    Union[  # noqa: UP007 — usamos Union explícito para que Annotated funcione bien
        TitleSlide,
        ContentSlide,
        SectionHeaderSlide,
        TwoContentSlide,
        ImageSlide,
        BlankSlide,
    ],
    Field(discriminator="layout"),
]
"""Discriminated union: el campo `layout` decide qué variante validar."""


class SlideDeck(BaseModel):
    """Container para validar una lista de slides desde JSON.

    Usamos un wrapper porque Pydantic necesita un BaseModel root para
    validar discriminated unions dentro de una lista. El modelo emite
    el JSON como array, lo wrappeamos antes de validar.
    """

    slides: list[Slide]


# Mapa de layout name → índice de layout de python-pptx.
# Se usa para resolver `prs.slide_layouts[idx]`. Si un template tiene
# layouts en otro orden, el usuario puede pasar su propio template
# (ver generate_pptx_from_template).
LAYOUT_INDEX: dict[str, int] = {
    "title": 0,
    "content": 1,
    "section_header": 2,
    "two_content": 3,
    "image": 5,
    "blank": 6,
}
