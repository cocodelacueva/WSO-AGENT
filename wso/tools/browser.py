"""Browser bridge: tools que manejan el Chrome real del usuario vía CDP.

El agente NO arranca un Chrome propio: se attachea (Playwright
`connect_over_cdp`) a uno que el usuario ya inició con remote debugging
(ver `scripts/launch-chrome-cdp.sh`). Así reutiliza las cookies y sesiones
logueadas del usuario — LinkedIn / Sales Navigator ven un navegador real.

Tools (ver DESIGN.md, Apéndice A):

    - browser_open_tab    : abre una tab nueva y la deja activa
    - browser_navigate    : navega la tab activa a una URL
    - browser_close_tab   : cierra la tab activa
    - browser_read_page   : texto visible del DOM de la tab activa (READ)
    - browser_screenshot  : guarda un PNG de la tab activa (READ; path validado)
    - browser_click       : click en un selector (CSS o `text=...`)
    - browser_type        : escribe texto en un input
    - browser_wait_for    : espera a que aparezca un selector (READ, SPA-friendly)

Threading
---------
El loop de WSO ejecuta las tools de forma SÍNCRONA dentro del thread que
corre el event loop de asyncio. La `sync_api` de Playwright se niega a
operar si hay un event loop corriendo en el mismo thread. Por eso todas
las operaciones de browser se despachan a un worker thread dedicado
(`_BrowserWorker`) que es dueño de la conexión CDP y de los objetos de
Playwright (page/context/browser), que además NO son thread-safe y deben
usarse siempre desde el thread que los creó.

Estado
------
El cliente (browser, context, active_page) es estado global del módulo con
lazy-init en el primer call. Cleanup registrado en `atexit`. La URL del
endpoint CDP sale de `settings.browser_cdp_url` (env `WSO_BROWSER_CDP_URL`).

Permisos
--------
Las tools de acción/navegación son categoría BROWSER (nunca auto-aprobadas
por default; sticky por dominio a nivel de sesión — ver permissions/manager).
`browser_read_page` y `browser_wait_for` son READ. `browser_screenshot` es
READ pero su `output_path` se valida como cualquier escritura.
"""

from __future__ import annotations

import atexit
import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wso.config import settings
from wso.tools.base import PermissionCategory, tool, truncate_with_notice

# Timeout default (ms) para clicks/typing/navegación que esperan elementos.
_ACTION_TIMEOUT_MS = 15000


# ---------------------------------------------------------------------------
# Dependencia opcional: Playwright
# ---------------------------------------------------------------------------


def _require_playwright() -> Any:
    """Importar Playwright con un mensaje claro si falta la dep."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        return sync_playwright
    except ImportError as e:  # pragma: no cover — depende del entorno
        raise ImportError(
            "Playwright no está instalado. Para usar las tools de browser, "
            'instalá la extra: `pip install -e ".[browser]"` o '
            "`pip install playwright`. El agente se attachea a tu Chrome real "
            "(no instala uno propio); arrancalo con scripts/launch-chrome-cdp.sh."
        ) from e


def _connect_error(exc: Exception, cdp_url: str) -> RuntimeError:
    """Envolver un fallo de attach en un error accionable para el modelo."""
    return RuntimeError(
        f"No pude attachearme al Chrome por CDP en {cdp_url}. "
        f"¿Arrancaste Chrome con scripts/launch-chrome-cdp.sh? "
        f"Verificá abriendo {cdp_url}/json/version en una tab. "
        f"(detalle: {type(exc).__name__}: {exc})"
    )


# ---------------------------------------------------------------------------
# Worker thread: dueño de la conexión y los objetos de Playwright
# ---------------------------------------------------------------------------


class _BrowserWorker:
    """Thread dedicado que opera Playwright sync_api fuera del event loop.

    Cada operación se encola como un callable `fn(worker)` y se ejecuta en
    el thread del worker, que mantiene `browser`, `context` y `active_page`.
    El caller bloquea hasta el resultado y re-lanza cualquier excepción.
    """

    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url
        self._tasks: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="wso-browser", daemon=True
        )
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._started = False

        # Estado de Playwright (vive solo en el thread del worker).
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.active_page: Any = None

    # ---- arranque / conexión ----

    def _ensure_started(self) -> None:
        if self._started:
            if self._start_error is not None:
                raise self._start_error
            return
        self._started = True
        self._thread.start()
        self._ready.wait()
        if self._start_error is not None:
            raise self._start_error

    def _run(self) -> None:
        """Body del thread: conecta y luego procesa la cola de tareas."""
        try:
            sync_playwright = _require_playwright()
            self._pw = sync_playwright().start()
            self.browser = self._pw.chromium.connect_over_cdp(self._cdp_url)
            contexts = self.browser.contexts
            self.context = contexts[0] if contexts else self.browser.new_context()
            pages = self.context.pages
            self.active_page = pages[-1] if pages else self.context.new_page()
        except Exception as e:  # noqa: BLE001 — guardamos para re-lanzar en el caller
            self._start_error = (
                e if isinstance(e, ImportError) else _connect_error(e, self._cdp_url)
            )
            self._ready.set()
            return

        self._ready.set()

        while True:
            item = self._tasks.get()
            if item is None:  # señal de shutdown
                break
            fn, box, done = item
            try:
                box["result"] = fn(self)
            except Exception as e:  # noqa: BLE001 — se re-lanza en el caller
                box["error"] = e
            finally:
                done.set()

        # Cleanup dentro del propio thread.
        try:
            if self.browser is not None:
                self.browser.close()  # solo cierra la conexión CDP, no tu Chrome
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001 — best-effort en shutdown
            pass

    # ---- API ----

    def submit(self, fn: Any) -> Any:
        """Ejecutar `fn(worker)` en el thread del worker y devolver su resultado."""
        self._ensure_started()
        box: dict[str, Any] = {}
        done = threading.Event()
        self._tasks.put((fn, box, done))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def shutdown(self) -> None:
        if self._started and self._start_error is None:
            self._tasks.put(None)


# ---------------------------------------------------------------------------
# Singleton del módulo
# ---------------------------------------------------------------------------

_worker: _BrowserWorker | None = None


def _session() -> _BrowserWorker:
    """Obtener (o lazy-crear) el worker del browser."""
    global _worker
    if _worker is None:
        _worker = _BrowserWorker(settings.browser_cdp_url)
        atexit.register(_worker.shutdown)
    return _worker


def _run(op: Any) -> Any:
    """Despachar una operación al worker. Extraído para testeabilidad."""
    _require_playwright()
    return _session().submit(op)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str:
    """Validar que la URL tenga scheme http/https."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL inválida: {url!r}. Debe empezar con http:// o https://."
        )
    if not parsed.netloc:
        raise ValueError(f"URL sin host: {url!r}.")
    return url


def _validate_absolute(path: str) -> Path:
    """Expandir `~` y verificar que el path sea absoluto."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(
            f"La ruta debe ser absoluta: {path!r}. Usá '/Users/...' o '~/...'."
        )
    return p


def _live_page(worker: _BrowserWorker) -> Any:
    """Devolver una page usable, recreando si la activa fue cerrada."""
    page = worker.active_page
    if page is None or page.is_closed():
        pages = worker.context.pages
        page = pages[-1] if pages else worker.context.new_page()
        worker.active_page = page
    return page


def _normalize_text(raw: str | None) -> str:
    """Normalizar innerText: limpia espacios por línea y colapsa líneas vacías."""
    if not raw:
        return ""
    lines = [ln.strip() for ln in raw.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):  # permite una sola línea en blanco entre bloques
            out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(
    name="browser_open_tab",
    category=PermissionCategory.BROWSER,
    description=(
        "Abre una tab nueva en tu Chrome y navega a la URL dada, dejándola "
        "como tab activa. Usá esto para empezar una tarea de browsing."
    ),
    args_schema={"url": "URL completa a abrir (http:// o https://)"},
)
def browser_open_tab(url: str) -> str:
    """Abrir una tab nueva y navegar."""
    _validate_url(url)

    def _op(w: _BrowserWorker) -> str:
        page = w.context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=_ACTION_TIMEOUT_MS)
        w.active_page = page
        return f"Tab nueva abierta y activa: {page.url} ({page.title()!r})"

    return _run(_op)


@tool(
    name="browser_navigate",
    category=PermissionCategory.BROWSER,
    description="Navega la tab activa a una URL nueva (sin abrir otra tab).",
    args_schema={"url": "URL completa a la que navegar (http:// o https://)"},
)
def browser_navigate(url: str) -> str:
    """Navegar la tab activa."""
    _validate_url(url)

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        page.goto(url, wait_until="domcontentloaded", timeout=_ACTION_TIMEOUT_MS)
        return f"Navegado a: {page.url} ({page.title()!r})"

    return _run(_op)


@tool(
    name="browser_close_tab",
    category=PermissionCategory.BROWSER,
    description=(
        "Cierra la tab activa. La tab activa pasa a ser otra que siga abierta "
        "(o una nueva en blanco si no queda ninguna). No cierra tu Chrome."
    ),
    args_schema={},
)
def browser_close_tab() -> str:
    """Cerrar la tab activa."""

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        closed_url = page.url
        page.close()
        remaining = w.context.pages
        w.active_page = remaining[-1] if remaining else w.context.new_page()
        return (
            f"Tab cerrada ({closed_url}). "
            f"Tab activa ahora: {w.active_page.url}"
        )

    return _run(_op)


@tool(
    name="browser_read_page",
    category=PermissionCategory.READ,
    description=(
        "Devuelve el texto visible del DOM de la tab activa (sin scripts ni "
        "navegación). Útil para leer/resumir lo que está en pantalla. En SPAs "
        "(como LinkedIn) puede convenir un browser_wait_for antes."
    ),
    args_schema={
        "max_chars": "tope de caracteres a devolver (default 16000; trunca con aviso)",
    },
)
def browser_read_page(max_chars: int = 16000) -> str:
    """Leer el texto visible de la tab activa."""

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        raw = page.evaluate("() => document.body ? document.body.innerText : ''")
        text = _normalize_text(raw)
        if not text:
            return (
                "(la página no tiene texto visible todavía: puede ser un SPA sin "
                "renderizar, una pantalla de login, o una tab en blanco. Probá "
                "browser_wait_for con un selector, o verificá que estés logueado.)"
            )
        header = f"[{page.title()!r} — {page.url}]\n\n"
        return header + truncate_with_notice(
            text,
            max_chars,
            what="la página",
            more_hint="Scrolleá o usá browser_wait_for si falta contenido.",
        )

    return _run(_op)


@tool(
    name="browser_screenshot",
    category=PermissionCategory.READ,
    description=(
        "Guarda un PNG de la tab activa en la ruta indicada. Útil para debug "
        "o para dejar evidencia de lo que el agente vio."
    ),
    args_schema={"output_path": "ruta absoluta del .png a guardar"},
)
def browser_screenshot(output_path: str) -> str:
    """Capturar la tab activa a un archivo PNG."""
    p = _validate_absolute(output_path)

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        p.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(p))
        return f"Screenshot guardado en {p}"

    return _run(_op)


@tool(
    name="browser_click",
    category=PermissionCategory.BROWSER,
    description=(
        "Hace click en el primer elemento que matchea el selector. El selector "
        "puede ser CSS (ej: 'button.submit') o texto de Playwright "
        "(ej: 'text=Enviar'). Hace scroll-into-view automático."
    ),
    args_schema={"selector": "selector CSS o 'text=...' del elemento a clickear"},
)
def browser_click(selector: str) -> str:
    """Click en un elemento."""
    if not selector.strip():
        raise ValueError("El selector no puede estar vacío.")

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        try:
            page.click(selector, timeout=_ACTION_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001 — mensaje conciso para el modelo
            raise RuntimeError(
                f"No pude clickear {selector!r}: {_short(e)}. "
                f"¿El selector existe en la página actual? Probá browser_read_page "
                f"o browser_wait_for primero."
            ) from e
        return f"Click en {selector!r}. URL actual: {page.url}"

    return _run(_op)


@tool(
    name="browser_type",
    category=PermissionCategory.BROWSER,
    description=(
        "Escribe texto en un input/textarea. Limpia el campo antes de escribir. "
        "El selector puede ser CSS o 'text=...'."
    ),
    args_schema={
        "selector": "selector CSS o 'text=...' del input",
        "text": "texto a escribir en el campo",
    },
)
def browser_type(selector: str, text: str) -> str:
    """Escribir texto en un input."""
    if not selector.strip():
        raise ValueError("El selector no puede estar vacío.")

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        try:
            page.fill(selector, text, timeout=_ACTION_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"No pude escribir en {selector!r}: {_short(e)}. "
                f"¿Es un campo editable y visible?"
            ) from e
        return f"Escrito {len(text)} caracteres en {selector!r}."

    return _run(_op)


@tool(
    name="browser_scroll",
    category=PermissionCategory.READ,
    description=(
        "Hace scroll en la tab activa para revelar más contenido (útil en "
        "feeds/listados con carga perezosa o scroll infinito, como resultados "
        "de búsqueda de LinkedIn). direction: 'down' | 'up' | 'top' | 'bottom'. "
        "amount = cantidad de pantallas a desplazar (solo para up/down). Tras "
        "scrollear, usá browser_read_page para leer lo nuevo."
    ),
    args_schema={
        "direction": "dirección: down | up | top | bottom (default down)",
        "amount": "cuántas pantallas desplazar en up/down (default 1)",
    },
)
def browser_scroll(direction: str = "down", amount: int = 1) -> str:
    """Hacer scroll en la tab activa."""
    d = direction.strip().lower()
    if d not in ("down", "up", "top", "bottom"):
        raise ValueError(
            f"direction inválida: {direction!r}. Usá down | up | top | bottom."
        )
    steps = max(1, amount)

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        if d == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
        elif d == "bottom":
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        else:
            sign = 1 if d == "down" else -1
            page.evaluate(
                f"() => window.scrollBy(0, {sign} * window.innerHeight * 0.9 * {steps})"
            )
        # Darle tiempo al contenido lazy a cargar antes de seguir.
        page.wait_for_timeout(600)
        return (
            f"Scroll '{d}' ejecutado (amount={steps}). "
            f"Usá browser_read_page para leer el contenido nuevo."
        )

    return _run(_op)


@tool(
    name="browser_wait_for",
    category=PermissionCategory.READ,
    description=(
        "Espera a que aparezca un elemento en la tab activa, hasta timeout_ms. "
        "Útil en SPAs que renderizan asíncrono (LinkedIn, dashboards) antes de "
        "leer o clickear."
    ),
    args_schema={
        "selector": "selector CSS o 'text=...' a esperar",
        "timeout_ms": "tiempo máximo de espera en ms (default 8000)",
    },
)
def browser_wait_for(selector: str, timeout_ms: int = 8000) -> str:
    """Esperar a que un selector esté presente."""
    if not selector.strip():
        raise ValueError("El selector no puede estar vacío.")

    def _op(w: _BrowserWorker) -> str:
        page = _live_page(w)
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001
            raise TimeoutError(
                f"El elemento {selector!r} no apareció en {timeout_ms}ms: "
                f"{_short(e)}."
            ) from e
        return f"Elemento {selector!r} presente en la página."

    return _run(_op)


def _short(exc: Exception, limit: int = 160) -> str:
    """Primera línea acotada del mensaje de una excepción de Playwright."""
    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"
