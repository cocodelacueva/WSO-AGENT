"""Tests de las tools de browser (Playwright mockeado).

No levantan un navegador real: reemplazan el worker y la page de Playwright
por fakes que registran las llamadas, para verificar que cada tool mapea sus
args a las operaciones correctas y formatea el resultado esperado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.tools import browser
from wso.tools.base import PermissionCategory, get_tool_definition

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(
        self, url: str = "https://example.com/", title: str = "Example",
        text: str = "Hola mundo",
    ) -> None:
        self._url = url
        self._title = title
        self._text = text
        self._closed = False
        self.calls: list[tuple] = []
        self.raise_on_click = False
        self.raise_on_fill = False
        self.raise_on_wait = False

    @property
    def url(self) -> str:
        return self._url

    def title(self) -> str:
        return self._title

    def is_closed(self) -> bool:
        return self._closed

    def goto(self, url: str, **kw) -> None:
        self._url = url
        self.calls.append(("goto", url))

    def evaluate(self, script: str) -> str:
        return self._text

    def click(self, selector: str, **kw) -> None:
        if self.raise_on_click:
            raise RuntimeError("Timeout 15000ms exceeded.\nlocator not found")
        self.calls.append(("click", selector))

    def fill(self, selector: str, text: str, **kw) -> None:
        if self.raise_on_fill:
            raise RuntimeError("not an editable element")
        self.calls.append(("fill", selector, text))

    def wait_for_selector(self, selector: str, **kw) -> None:
        if self.raise_on_wait:
            raise RuntimeError("Timeout exceeded")
        self.calls.append(("wait", selector, kw.get("timeout")))

    def screenshot(self, path: str) -> None:
        self.calls.append(("screenshot", path))

    def close(self) -> None:
        self._closed = True


class FakeContext:
    def __init__(self, worker: FakeWorker) -> None:
        self._w = worker

    @property
    def pages(self) -> list[FakePage]:
        return [p for p in self._w.all_pages if not p.is_closed()]

    def new_page(self) -> FakePage:
        p = FakePage(url="about:blank", title="", text="")
        self._w.all_pages.append(p)
        return p


class FakeWorker:
    def __init__(self) -> None:
        self.active_page = FakePage()
        self.all_pages: list[FakePage] = [self.active_page]
        self.context = FakeContext(self)

    def submit(self, fn):
        return fn(self)


@pytest.fixture
def fake(monkeypatch) -> FakeWorker:
    w = FakeWorker()
    monkeypatch.setattr(browser, "_require_playwright", lambda: None)
    monkeypatch.setattr(browser, "_session", lambda: w)
    return w


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_validate_url_accepts_http_https(self) -> None:
        assert browser._validate_url("https://x.com/y") == "https://x.com/y"
        assert browser._validate_url("http://x.com") == "http://x.com"

    @pytest.mark.parametrize("bad", ["ftp://x.com", "javascript:alert(1)", "x.com", ""])
    def test_validate_url_rejects_bad_scheme(self, bad: str) -> None:
        with pytest.raises(ValueError):
            browser._validate_url(bad)

    def test_validate_absolute_rejects_relative(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            browser._validate_absolute("rel/path.png")

    def test_normalize_text_collapses_blank_lines(self) -> None:
        raw = "  a  \n\n\n  b \n\n"
        assert browser._normalize_text(raw) == "a\n\nb"

    def test_normalize_text_empty(self) -> None:
        assert browser._normalize_text("") == ""
        assert browser._normalize_text(None) == ""


# ---------------------------------------------------------------------------
# Metadata de las tools
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_categories(self) -> None:
        defs = {
            name: get_tool_definition(getattr(browser, name))
            for name in [
                "browser_open_tab",
                "browser_navigate",
                "browser_close_tab",
                "browser_click",
                "browser_type",
                "browser_read_page",
                "browser_screenshot",
                "browser_wait_for",
            ]
        }
        for name in ["browser_open_tab", "browser_navigate", "browser_close_tab",
                     "browser_click", "browser_type"]:
            assert defs[name].category == PermissionCategory.BROWSER
        for name in ["browser_read_page", "browser_screenshot", "browser_wait_for"]:
            assert defs[name].category == PermissionCategory.READ


# ---------------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------------


class TestNavigation:
    def test_open_tab_creates_and_activates(self, fake: FakeWorker) -> None:
        result = browser.browser_open_tab("https://acme.com/landing")
        # Se creó una tab nueva y quedó activa
        assert fake.active_page.url == "https://acme.com/landing"
        assert ("goto", "https://acme.com/landing") in fake.active_page.calls
        assert "acme.com/landing" in result

    def test_open_tab_validates_url(self, fake: FakeWorker) -> None:
        with pytest.raises(ValueError):
            browser.browser_open_tab("notaurl")

    def test_navigate_uses_active_page(self, fake: FakeWorker) -> None:
        original = fake.active_page
        browser.browser_navigate("https://acme.com/x")
        assert original.url == "https://acme.com/x"
        # No se creó tab nueva
        assert len(fake.all_pages) == 1

    def test_close_tab_switches_active(self, fake: FakeWorker) -> None:
        # Abrimos una segunda tab, luego la cerramos
        browser.browser_open_tab("https://acme.com/2")
        assert len(fake.context.pages) == 2
        result = browser.browser_close_tab()
        assert len(fake.context.pages) == 1
        assert "Tab cerrada" in result


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


class TestReading:
    def test_read_page_returns_text_with_header(self, fake: FakeWorker) -> None:
        fake.active_page._text = "Contenido real de la página"
        result = browser.browser_read_page()
        assert "Contenido real" in result
        assert "example.com" in result  # header con la URL

    def test_read_page_empty_returns_placeholder(self, fake: FakeWorker) -> None:
        fake.active_page._text = ""
        result = browser.browser_read_page()
        assert "no tiene texto visible" in result

    def test_read_page_truncates(self, fake: FakeWorker) -> None:
        fake.active_page._text = "x" * 5000
        result = browser.browser_read_page(max_chars=100)
        assert "TRUNCADO" in result

    def test_wait_for_calls_selector(self, fake: FakeWorker) -> None:
        result = browser.browser_wait_for("div.feed", timeout_ms=3000)
        assert ("wait", "div.feed", 3000) in fake.active_page.calls
        assert "presente" in result

    def test_wait_for_timeout_raises(self, fake: FakeWorker) -> None:
        fake.active_page.raise_on_wait = True
        with pytest.raises(TimeoutError):
            browser.browser_wait_for("div.never")


# ---------------------------------------------------------------------------
# Acciones
# ---------------------------------------------------------------------------


class TestActions:
    def test_click_maps_selector(self, fake: FakeWorker) -> None:
        browser.browser_click("button.connect")
        assert ("click", "button.connect") in fake.active_page.calls

    def test_click_empty_selector_raises(self, fake: FakeWorker) -> None:
        with pytest.raises(ValueError):
            browser.browser_click("   ")

    def test_click_failure_wrapped(self, fake: FakeWorker) -> None:
        fake.active_page.raise_on_click = True
        with pytest.raises(RuntimeError, match="No pude clickear"):
            browser.browser_click("button.x")

    def test_type_fills_field(self, fake: FakeWorker) -> None:
        browser.browser_type("input#search", "diseño")
        assert ("fill", "input#search", "diseño") in fake.active_page.calls

    def test_type_failure_wrapped(self, fake: FakeWorker) -> None:
        fake.active_page.raise_on_fill = True
        with pytest.raises(RuntimeError, match="No pude escribir"):
            browser.browser_type("input#x", "y")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


class TestScreenshot:
    def test_screenshot_saves_to_path(self, fake: FakeWorker, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "shot.png"
        result = browser.browser_screenshot(str(out))
        assert ("screenshot", str(out)) in fake.active_page.calls
        assert out.parent.exists()  # creó el directorio padre
        assert str(out) in result

    def test_screenshot_rejects_relative(self, fake: FakeWorker) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            browser.browser_screenshot("rel.png")
