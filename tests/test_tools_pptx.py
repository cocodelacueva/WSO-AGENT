"""Tests de las tools de PPTX.

Cubre:
    - Schemas Pydantic (TitleSlide, ContentSlide, ..., discriminator)
    - generate_pptx: happy path por layout, errores de path/json/schema
    - read_pptx: extracción de títulos, bullets, notas
    - edit_pptx_slide: actualizaciones puntuales y preservación del resto
    - generate_pptx_from_template: uso de template y reemplazo de slides

Todos los tests usan `tmp_path` para aislamiento. Si python-pptx no está
instalado, el módulo entero se skipea con un mensaje claro.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip todo el módulo si python-pptx no está disponible. Si querés
# correr estos tests, instalá la extra: `pip install -e ".[office]"`.
pptx = pytest.importorskip("pptx")

from pydantic import ValidationError  # noqa: E402

from wso.tools.pptx import (  # noqa: E402
    _parse_slides_json,
    edit_pptx_slide,
    generate_pptx,
    generate_pptx_from_template,
    read_pptx,
)
from wso.tools.pptx_schemas import (  # noqa: E402
    BlankSlide,
    ContentSlide,
    ImageSlide,
    SectionHeaderSlide,
    SlideDeck,
    TitleSlide,
    TwoContentSlide,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_simple_deck(tmp_path: Path, slides: list[dict]) -> Path:
    """Helper: generar un deck con la estructura dada y devolver la ruta."""
    target = tmp_path / "deck.pptx"
    generate_pptx(str(target), json.dumps(slides))
    return target


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_title_slide_minimal(self) -> None:
        s = TitleSlide(layout="title", title="Hola")
        assert s.title == "Hola"
        assert s.subtitle == ""
        assert s.notes == ""

    def test_content_slide_with_bullets(self) -> None:
        s = ContentSlide(layout="content", title="X", bullets=["a", "b"])
        assert s.bullets == ["a", "b"]

    def test_two_content_slide_columns(self) -> None:
        s = TwoContentSlide(
            layout="two_content",
            title="X",
            left_bullets=["a"],
            right_bullets=["b"],
        )
        assert s.left_bullets == ["a"]
        assert s.right_bullets == ["b"]

    def test_image_slide_requires_path(self) -> None:
        with pytest.raises(ValidationError):
            ImageSlide(layout="image", title="X")  # type: ignore[call-arg]

    def test_blank_slide_no_required_fields(self) -> None:
        s = BlankSlide(layout="blank")
        assert s.title == ""

    def test_section_header(self) -> None:
        s = SectionHeaderSlide(layout="section_header", title="Anexos")
        assert s.title == "Anexos"

    def test_deck_validates_discriminator(self) -> None:
        deck = SlideDeck(
            slides=[
                {"layout": "title", "title": "T"},
                {"layout": "content", "title": "C", "bullets": ["a"]},
            ]
        )
        assert isinstance(deck.slides[0], TitleSlide)
        assert isinstance(deck.slides[1], ContentSlide)

    def test_deck_rejects_unknown_layout(self) -> None:
        with pytest.raises(ValidationError):
            SlideDeck(slides=[{"layout": "unknown_layout", "title": "X"}])

    def test_deck_rejects_missing_required_field(self) -> None:
        # ContentSlide tiene title como required
        with pytest.raises(ValidationError):
            SlideDeck(slides=[{"layout": "content"}])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# generate_pptx
# ---------------------------------------------------------------------------


class TestGeneratePptx:
    def test_creates_minimal_deck(self, tmp_path: Path) -> None:
        target = tmp_path / "out.pptx"
        slides = [{"layout": "title", "title": "Hola", "subtitle": "2026"}]
        result = generate_pptx(str(target), json.dumps(slides))

        assert target.exists()
        assert "Generado" in result
        assert "1 slide" in result

    def test_pluralizes_slide_count(self, tmp_path: Path) -> None:
        target = tmp_path / "multi.pptx"
        slides = [
            {"layout": "title", "title": "A"},
            {"layout": "content", "title": "B", "bullets": ["x"]},
            {"layout": "blank"},
        ]
        result = generate_pptx(str(target), json.dumps(slides))
        assert "3 slides" in result

    def test_all_layouts_render(self, tmp_path: Path) -> None:
        target = tmp_path / "all.pptx"
        # No incluimos 'image' acá porque requiere archivo real;
        # se testea por separado.
        slides = [
            {"layout": "title", "title": "T1", "subtitle": "Sub"},
            {"layout": "content", "title": "T2", "bullets": ["a", "b"]},
            {"layout": "section_header", "title": "T3"},
            {
                "layout": "two_content",
                "title": "T4",
                "left_bullets": ["L1"],
                "right_bullets": ["R1"],
            },
            {"layout": "blank", "title": "T5"},
        ]
        generate_pptx(str(target), json.dumps(slides))

        # Verificar leyéndolo de vuelta
        prs = pptx.Presentation(str(target))
        assert len(prs.slides) == 5

    def test_speaker_notes_persisted(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.pptx"
        slides = [
            {
                "layout": "content",
                "title": "Con notas",
                "bullets": ["punto 1"],
                "notes": "Hablar despacio acá.",
            }
        ]
        generate_pptx(str(target), json.dumps(slides))

        prs = pptx.Presentation(str(target))
        notes_text = prs.slides[0].notes_slide.notes_text_frame.text
        assert "Hablar despacio acá." in notes_text

    def test_bullets_become_paragraphs(self, tmp_path: Path) -> None:
        target = tmp_path / "bullets.pptx"
        slides = [
            {
                "layout": "content",
                "title": "Bullets",
                "bullets": ["uno", "dos", "tres"],
            }
        ]
        generate_pptx(str(target), json.dumps(slides))

        prs = pptx.Presentation(str(target))
        body = next(
            ph for ph in prs.slides[0].placeholders if ph != prs.slides[0].shapes.title
        )
        texts = [p.text for p in body.text_frame.paragraphs]
        assert texts == ["uno", "dos", "tres"]

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "deck.pptx"
        slides = [{"layout": "title", "title": "X"}]
        generate_pptx(str(target), json.dumps(slides))
        assert target.exists()

    def test_accepts_object_with_slides_key(self, tmp_path: Path) -> None:
        target = tmp_path / "wrapped.pptx"
        wrapped = {"slides": [{"layout": "title", "title": "X"}]}
        generate_pptx(str(target), json.dumps(wrapped))
        assert target.exists()

    def test_image_slide_with_real_image(self, tmp_path: Path) -> None:
        # Generar una imagen PNG mínima (1x1 px) sin dependencias externas.
        # Este es un PNG válido más chico posible.
        image = tmp_path / "tiny.png"
        image.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xa8\x8f"
            b"\xee"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        target = tmp_path / "img.pptx"
        slides = [
            {
                "layout": "image",
                "title": "Imagen",
                "image_path": str(image),
                "caption": "Foto chiquita",
            }
        ]
        generate_pptx(str(target), json.dumps(slides))
        assert target.exists()

        # La caption se mete en speaker notes
        prs = pptx.Presentation(str(target))
        assert "Foto chiquita" in prs.slides[0].notes_slide.notes_text_frame.text

    # ---- Errores ----

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            generate_pptx("rel.pptx", json.dumps([{"layout": "title", "title": "X"}]))

    def test_directory_as_output_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            generate_pptx(str(tmp_path), json.dumps([{"layout": "title", "title": "X"}]))

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        with pytest.raises(json.JSONDecodeError):
            generate_pptx(str(tmp_path / "x.pptx"), "{ not valid json")

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        # Falta el campo 'title' requerido para layout 'content'. La
        # normalización no puede inventarlo, así que se levanta un ValueError
        # con un mensaje guía (en vez del ValidationError crudo de pydantic).
        with pytest.raises(ValueError, match="layout"):
            generate_pptx(
                str(tmp_path / "x.pptx"),
                json.dumps([{"layout": "content"}]),
            )

    def test_unknown_layout_coerced_to_content(self, tmp_path: Path) -> None:
        # Un layout desconocido ya no explota: se infiere uno razonable
        # (content, porque tiene title y nada que sugiera otra cosa).
        target = tmp_path / "x.pptx"
        result = generate_pptx(
            str(target),
            json.dumps([{"layout": "carousel", "title": "X"}]),
        )
        assert "Generado" in result
        assert target.exists()

    def test_missing_image_raises(self, tmp_path: Path) -> None:
        slides = [
            {
                "layout": "image",
                "title": "X",
                "image_path": str(tmp_path / "no_existe.png"),
            }
        ]
        with pytest.raises(FileNotFoundError):
            generate_pptx(str(tmp_path / "out.pptx"), json.dumps(slides))


# ---------------------------------------------------------------------------
# read_pptx
# ---------------------------------------------------------------------------


class TestReadPptx:
    def test_extracts_title_and_bullets(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [
                {"layout": "title", "title": "Mi Deck", "subtitle": "v1"},
                {"layout": "content", "title": "Puntos", "bullets": ["a", "b"]},
            ],
        )

        result = read_pptx(str(target))
        assert "Mi Deck" in result
        assert "v1" in result
        assert "Puntos" in result
        assert "a" in result
        assert "b" in result
        assert "Slide 0" in result
        assert "Slide 1" in result

    def test_includes_speaker_notes(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [
                {
                    "layout": "content",
                    "title": "T",
                    "bullets": ["x"],
                    "notes": "Acordate del timing.",
                }
            ],
        )
        result = read_pptx(str(target))
        assert "Notas:" in result
        assert "Acordate del timing." in result

    def test_reports_slide_count(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [{"layout": "blank"}, {"layout": "blank"}, {"layout": "blank"}],
        )
        result = read_pptx(str(target))
        assert "Slides: 3" in result

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            read_pptx("relativo.pptx")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_pptx(str(tmp_path / "nope.pptx"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_pptx(str(tmp_path))


# ---------------------------------------------------------------------------
# edit_pptx_slide
# ---------------------------------------------------------------------------


class TestEditPptxSlide:
    def test_updates_title(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [{"layout": "content", "title": "Viejo", "bullets": ["x"]}],
        )

        edit_pptx_slide(str(target), 0, json.dumps({"title": "Nuevo"}))

        result = read_pptx(str(target))
        assert "Nuevo" in result
        assert "Viejo" not in result

    def test_updates_bullets(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [{"layout": "content", "title": "T", "bullets": ["uno", "dos"]}],
        )

        edit_pptx_slide(str(target), 0, json.dumps({"bullets": ["nuevo"]}))

        result = read_pptx(str(target))
        assert "nuevo" in result
        # Los viejos bullets fueron reemplazados
        assert "uno" not in result
        assert "dos" not in result

    def test_updates_notes(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [{"layout": "content", "title": "T", "bullets": ["x"], "notes": "viejo"}],
        )
        edit_pptx_slide(str(target), 0, json.dumps({"notes": "nuevo note"}))

        result = read_pptx(str(target))
        assert "nuevo note" in result
        assert "viejo" not in result

    def test_preserves_other_slides(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path,
            [
                {"layout": "title", "title": "Slide A"},
                {"layout": "content", "title": "Slide B", "bullets": ["x"]},
                {"layout": "content", "title": "Slide C", "bullets": ["y"]},
            ],
        )
        edit_pptx_slide(str(target), 1, json.dumps({"title": "B modificado"}))

        result = read_pptx(str(target))
        assert "Slide A" in result
        assert "B modificado" in result
        assert "Slide C" in result
        assert "Slide B" not in result  # el viejo fue reemplazado

    def test_no_keys_returns_no_change_message(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        result = edit_pptx_slide(str(target), 0, json.dumps({}))
        assert "Sin cambios" in result

    def test_index_out_of_range_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        with pytest.raises(IndexError):
            edit_pptx_slide(str(target), 5, json.dumps({"title": "Y"}))

    def test_negative_index_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        with pytest.raises(IndexError):
            edit_pptx_slide(str(target), -1, json.dumps({"title": "Y"}))

    def test_invalid_updates_json_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        with pytest.raises(ValueError, match="JSON"):
            edit_pptx_slide(str(target), 0, "{ no es json")

    def test_unsupported_key_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        with pytest.raises(ValueError, match="no soportadas"):
            edit_pptx_slide(
                str(target), 0, json.dumps({"unsupported_field": "x"})
            )

    def test_non_object_updates_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "title", "title": "X"}]
        )
        with pytest.raises(ValueError, match="objeto JSON"):
            edit_pptx_slide(str(target), 0, json.dumps(["lista", "no", "objeto"]))

    def test_bullets_non_list_raises(self, tmp_path: Path) -> None:
        target = _build_simple_deck(
            tmp_path, [{"layout": "content", "title": "X", "bullets": ["a"]}]
        )
        with pytest.raises(ValueError, match="bullets"):
            edit_pptx_slide(
                str(target), 0, json.dumps({"bullets": "no soy lista"})
            )

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            edit_pptx_slide(
                str(tmp_path / "nope.pptx"), 0, json.dumps({"title": "X"})
            )


# ---------------------------------------------------------------------------
# generate_pptx_from_template
# ---------------------------------------------------------------------------


class TestGenerateFromTemplate:
    def test_uses_template_and_replaces_slides(self, tmp_path: Path) -> None:
        # Primero crear un "template" con un par de slides
        template = tmp_path / "template.pptx"
        generate_pptx(
            str(template),
            json.dumps(
                [
                    {"layout": "title", "title": "Template Slide A"},
                    {"layout": "content", "title": "Template Slide B", "bullets": ["x"]},
                ]
            ),
        )

        # Generar uno nuevo desde ese template con slides distintos
        target = tmp_path / "from_template.pptx"
        new_slides = [
            {"layout": "title", "title": "Nuevo A"},
            {"layout": "content", "title": "Nuevo B", "bullets": ["y"]},
        ]
        generate_pptx_from_template(
            str(template), str(target), json.dumps(new_slides)
        )

        assert target.exists()
        result = read_pptx(str(target))
        # Los nuevos slides están
        assert "Nuevo A" in result
        assert "Nuevo B" in result
        # Los viejos del template no
        assert "Template Slide A" not in result
        assert "Template Slide B" not in result

    def test_returns_count_and_template_name(self, tmp_path: Path) -> None:
        template = tmp_path / "tpl.pptx"
        generate_pptx(str(template), json.dumps([{"layout": "blank"}]))

        target = tmp_path / "out.pptx"
        result = generate_pptx_from_template(
            str(template),
            str(target),
            json.dumps([{"layout": "title", "title": "X"}, {"layout": "blank"}]),
        )
        assert "tpl.pptx" in result
        assert "2 slides" in result

    def test_missing_template_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate_pptx_from_template(
                str(tmp_path / "nope.pptx"),
                str(tmp_path / "out.pptx"),
                json.dumps([{"layout": "title", "title": "X"}]),
            )

    def test_relative_output_path_rejected(self, tmp_path: Path) -> None:
        template = tmp_path / "t.pptx"
        generate_pptx(str(template), json.dumps([{"layout": "blank"}]))

        with pytest.raises(ValueError, match="absoluta"):
            generate_pptx_from_template(
                str(template), "rel.pptx", json.dumps([{"layout": "blank"}])
            )

    def test_invalid_slides_json_raises(self, tmp_path: Path) -> None:
        template = tmp_path / "t.pptx"
        generate_pptx(str(template), json.dumps([{"layout": "blank"}]))

        with pytest.raises(json.JSONDecodeError):
            generate_pptx_from_template(
                str(template), str(tmp_path / "out.pptx"), "{ broken"
            )


# ---------------------------------------------------------------------------
# Normalización tolerante de slides (robustez frente a modelos chicos)
# ---------------------------------------------------------------------------


class TestSlideNormalization:
    """El parser de slides debe tolerar las variaciones que emiten los
    modelos locales: layout omitido, `content` en vez de `bullets`,
    sinónimos de layout, bullets como string, un slide suelto, etc.

    Reproduce los modos de falla observados en los logs de la prueba 2.
    """

    def test_missing_layout_with_content_string(self) -> None:
        # Lo que emitió el modelo local: sin layout, con `content` string.
        deck = _parse_slides_json(
            json.dumps([{"title": "Intro", "content": "Una presentación."}])
        )
        assert len(deck.slides) == 1
        slide = deck.slides[0]
        assert isinstance(slide, ContentSlide)
        assert slide.title == "Intro"
        assert slide.bullets == ["Una presentación."]

    def test_content_as_list_becomes_bullets(self) -> None:
        deck = _parse_slides_json(
            json.dumps([{"title": "X", "content": ["a", "b", "c"]}])
        )
        assert deck.slides[0].bullets == ["a", "b", "c"]

    def test_bullets_as_string_splits_on_newlines(self) -> None:
        deck = _parse_slides_json(
            json.dumps([{"layout": "content", "title": "Y", "bullets": "l1\nl2"}])
        )
        assert deck.slides[0].bullets == ["l1", "l2"]

    def test_layout_synonym_is_normalized(self) -> None:
        deck = _parse_slides_json(
            json.dumps([{"layout": "Portada", "title": "C", "subtitle": "2026"}])
        )
        assert isinstance(deck.slides[0], TitleSlide)
        assert deck.slides[0].subtitle == "2026"

    def test_single_slide_dict_is_wrapped(self) -> None:
        deck = _parse_slides_json(json.dumps({"layout": "blank", "title": "Solo"}))
        assert len(deck.slides) == 1
        assert isinstance(deck.slides[0], BlankSlide)

    def test_slides_wrapper_object(self) -> None:
        deck = _parse_slides_json(
            json.dumps({"slides": [{"layout": "content", "title": "T", "content": "c"}]})
        )
        assert deck.slides[0].bullets == ["c"]

    def test_two_content_string_sides_coerced(self) -> None:
        deck = _parse_slides_json(
            json.dumps(
                [
                    {
                        "layout": "two_content",
                        "title": "Cmp",
                        "left_bullets": "a\nb",
                        "right_bullets": ["c"],
                    }
                ]
            )
        )
        slide = deck.slides[0]
        assert isinstance(slide, TwoContentSlide)
        assert slide.left_bullets == ["a", "b"]
        assert slide.right_bullets == ["c"]

    def test_missing_title_raises_guided_valueerror(self) -> None:
        # No se puede inventar el título: error claro, no ValidationError crudo.
        with pytest.raises(ValueError, match="layout"):
            _parse_slides_json(json.dumps([{"layout": "content"}]))

    def test_normalized_deck_generates_file(self, tmp_path: Path) -> None:
        # End-to-end: la estructura "sucia" del modelo produce un .pptx real.
        target = tmp_path / "deck.pptx"
        result = generate_pptx(
            str(target),
            json.dumps(
                [
                    {"title": "Portada", "subtitle": "2026", "layout": "title"},
                    {"title": "Puntos", "content": "uno\ndos\ntres"},
                ]
            ),
        )
        assert target.exists()
        assert "2 slides" in result
