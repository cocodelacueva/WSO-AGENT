#!/usr/bin/env bash
#
# launch-chrome-cdp.sh — Arranca Google Chrome con el puerto de remote
# debugging (CDP) abierto, para que WSO pueda attachearse vía Playwright.
#
# Uso:
#   ./scripts/launch-chrome-cdp.sh              # perfil aislado (recomendado)
#   ./scripts/launch-chrome-cdp.sh --default    # tu perfil real (todas tus cuentas)
#
# Notas:
#   - El puerto default es 9222 (coincide con WSO_BROWSER_CDP_URL default).
#     Override con la env var CDP_PORT.
#   - Perfil aislado: Chrome arranca limpio en un user-data-dir separado
#     (~/.wso-chrome). Tenés que loguearte en LinkedIn/Sales Navigator una
#     vez ahí; queda persistido para próximas corridas.
#   - --default usa tu perfil real: pleno acceso a tus sesiones ya logueadas,
#     pero más riesgoso. Cerrá las otras ventanas de Chrome antes, porque
#     Chrome no abre un segundo proceso sobre el mismo user-data-dir.
#   - El flag --disable-blink-features=AutomationControlled reduce la
#     detección de automation (navigator.webdriver).

set -euo pipefail

CDP_PORT="${CDP_PORT:-9222}"
USE_DEFAULT_PROFILE=0

for arg in "$@"; do
  case "$arg" in
    --default) USE_DEFAULT_PROFILE=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Argumento desconocido: $arg" >&2; exit 1 ;;
  esac
done

# Detectar el binario de Chrome según la plataforma.
case "$(uname -s)" in
  Darwin)
    CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ;;
  Linux)
    CHROME="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || true)"
    ;;
  *)
    echo "Plataforma no soportada por este script. Usá launch-chrome-cdp.ps1 en Windows." >&2
    exit 1
    ;;
esac

if [[ -z "${CHROME:-}" || ! -e "$CHROME" ]]; then
  echo "No encontré el binario de Chrome. Editá la variable CHROME en este script." >&2
  exit 1
fi

ARGS=(
  --remote-debugging-port="$CDP_PORT"
  --disable-blink-features=AutomationControlled
  --no-first-run
  --no-default-browser-check
)

if [[ "$USE_DEFAULT_PROFILE" -eq 0 ]]; then
  PROFILE_DIR="${WSO_CHROME_PROFILE:-$HOME/.wso-chrome}"
  mkdir -p "$PROFILE_DIR"
  ARGS+=(--user-data-dir="$PROFILE_DIR")
  echo "→ Perfil aislado: $PROFILE_DIR"
else
  echo "→ Perfil DEFAULT de Chrome (tus sesiones reales). Cerrá las otras ventanas de Chrome primero."
fi

echo "→ Lanzando Chrome con CDP en http://localhost:$CDP_PORT"
echo "→ Dejá esta ventana de Chrome abierta y corré el spike en otra terminal."
exec "$CHROME" "${ARGS[@]}"
