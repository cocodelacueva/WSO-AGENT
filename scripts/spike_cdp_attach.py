#!/usr/bin/env python3
"""Spike de validación: Playwright + CDP attach al Chrome real.

Este script NO es parte del harness WSO. Es una prueba de concepto que
confirma (o descarta) la base técnica del browser bridge antes de codear
las tools. Ver DESIGN.md, Apéndice A.

Qué hace:
  1. Se conecta por CDP a un Chrome que vos ya iniciaste con remote
     debugging (usá scripts/launch-chrome-cdp.sh).
  2. Lista los contextos y las tabs abiertas (prueba que ve TU navegador,
     con tus sesiones logueadas).
  3. Lee título + URL de la tab activa, y los primeros N chars del texto
     visible del DOM.
  4. Opcionalmente navega a una URL que le pases y repite la lectura —
     útil para probar contra Sales Navigator con la sesión real.
  5. Reporta señales de detección de automation (navigator.webdriver).

Importante: NO arranca un Chrome propio ni cierra el tuyo. Solo se
attachea y lee. No hace clicks ni escribe nada.

Uso:
    # 1. En una terminal:
    ./scripts/launch-chrome-cdp.sh
    # (logueate en LinkedIn/Sales Navigator en esa ventana de Chrome)

    # 2. En otra terminal:
    pip install "playwright>=1.40"
    playwright install chromium        # solo si no attachea; ver nota abajo
    python scripts/spike_cdp_attach.py
    python scripts/spike_cdp_attach.py --url https://www.linkedin.com/sales/

Nota: para attach puro por CDP no hace falta `playwright install` (usa el
Chrome tuyo, no el bundled). Lo dejamos documentado por si querés que
Playwright maneje su propio binario en otro contexto.
"""

from __future__ import annotations

import argparse
import os
import sys

CDP_URL = os.environ.get("WSO_BROWSER_CDP_URL", "http://localhost:9222")
MAX_CHARS = 600


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike CDP attach para WSO browser bridge.")
    parser.add_argument(
        "--cdp-url",
        default=CDP_URL,
        help=f"URL del endpoint CDP (default: {CDP_URL}, o env WSO_BROWSER_CDP_URL).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Si se pasa, navega la tab activa a esta URL antes de leer.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS,
        help=f"Cuántos chars del texto visible imprimir (default {MAX_CHARS}).",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=8000,
        help="Tras navegar, cuánto esperar a que el SPA renderice texto (default 8000).",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: falta Playwright. Instalá con:\n"
            '    pip install "playwright>=1.40"',
            file=sys.stderr,
        )
        return 2

    print(f"[spike] Intentando attach por CDP a {args.cdp_url} ...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:  # noqa: BLE001 — spike, queremos el mensaje crudo
            print(
                f"[spike] FALLÓ el attach: {exc}\n\n"
                "Checklist:\n"
                "  - ¿Arrancaste Chrome con scripts/launch-chrome-cdp.sh?\n"
                "  - ¿El puerto coincide? (default 9222)\n"
                "  - Probá abrir http://localhost:9222/json/version en otra tab:\n"
                "    si no responde, Chrome no tiene CDP abierto.",
                file=sys.stderr,
            )
            return 1

        contexts = browser.contexts
        print(f"[spike] OK. Conectado. Contextos: {len(contexts)}")

        if not contexts:
            print("[spike] No hay contextos (raro). Abortando.", file=sys.stderr)
            browser.close()
            return 1

        context = contexts[0]
        pages = context.pages
        print(f"[spike] Tabs abiertas en el primer contexto: {len(pages)}")
        for i, pg in enumerate(pages):
            try:
                print(f"    [{i}] {pg.title()!r}  <- {pg.url}")
            except Exception as exc:  # noqa: BLE001
                print(f"    [{i}] (no pude leer: {exc})")

        # Elegir la tab a inspeccionar.
        page = pages[0] if pages else context.new_page()

        if args.url:
            print(f"\n[spike] Navegando la tab activa a: {args.url}")
            page.goto(args.url, wait_until="domcontentloaded")
            # LinkedIn es un SPA pesado: en domcontentloaded el body suele
            # estar vacío. Esperamos a que aparezca texto real (o timeout).
            # Esta misma lógica va a vivir en browser_read_page / browser_wait_for.
            print("[spike] Esperando a que el SPA renderice contenido ...")
            try:
                page.wait_for_function(
                    "() => document.body && document.body.innerText.trim().length > 50",
                    timeout=args.wait_ms,
                )
                print("[spike] Contenido detectado.")
            except Exception:  # noqa: BLE001 — timeout esperado si hay login wall
                print(
                    f"[spike] Timeout ({args.wait_ms}ms) sin contenido. "
                    "Probablemente estás viendo un login wall (perfil sin sesión)."
                )

        print("\n[spike] --- Tab activa ---")
        print(f"    title: {page.title()!r}")
        print(f"    url:   {page.url}")

        # Señal de detección de automation.
        try:
            webdriver_flag = page.evaluate("() => navigator.webdriver")
            print(f"    navigator.webdriver: {webdriver_flag}  "
                  f"(true = el sitio puede detectar automation)")
        except Exception as exc:  # noqa: BLE001
            print(f"    navigator.webdriver: (no evaluable: {exc})")

        # Texto visible del body (lo que haría browser_read_page).
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
            text = " ".join((text or "").split())
            snippet = text[: args.max_chars]
            print(f"\n[spike] Primeros {args.max_chars} chars del texto visible:\n")
            print(snippet if snippet else "(vacío)")
            if len(text) > args.max_chars:
                print(f"\n    ... ({len(text) - args.max_chars} chars más)")

            # Heurística de login wall sobre la URL + texto.
            low = (text or "").lower()
            login_signals = ("iniciar sesión", "sign in", "log in", "join now", "email or phone")
            looks_login = "login" in page.url.lower() or any(s in low for s in login_signals)
            if not text:
                print("\n[spike] DIAGNÓSTICO: body vacío → login wall o SPA sin sesión. "
                      "Logueate en esta ventana de Chrome y reintentá.")
            elif looks_login:
                print("\n[spike] DIAGNÓSTICO: parece pantalla de login. "
                      "Logueate en esta ventana de Chrome (queda persistido) y reintentá.")
            else:
                print("\n[spike] DIAGNÓSTICO: leíste contenido real (no login) → "
                      "browser bridge viable. ✓")
        except Exception as exc:  # noqa: BLE001
            print(f"[spike] No pude leer el texto del DOM: {exc}", file=sys.stderr)

        # IMPORTANTE: browser.close() sobre una conexión CDP solo cierra la
        # conexión de Playwright, NO mata tu Chrome. Tus tabs quedan abiertas.
        browser.close()
        print("\n[spike] Listo. Tu Chrome sigue abierto; solo se cerró la conexión CDP.")

    print(
        "\n[spike] VEREDICTO:\n"
        "  - Si arriba viste tus tabs reales y el texto de la página → CDP attach FUNCIONA.\n"
        "  - Probá ahora: python scripts/spike_cdp_attach.py --url https://www.linkedin.com/sales/\n"
        "    Si ves contenido de Sales Navigator (no login) → seguimos con browser.py.\n"
        "  - Si LinkedIn te muestra login/captcha o navigator.webdriver=true te limita → avisame y "
        "replanteamos el approach."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
