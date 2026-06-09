#!/usr/bin/env python3
"""Demo manual de run_python — ejercita la tool sin necesidad de un modelo.

Corre varios casos directos contra `wso.tools.code.run_python` y muestra la
salida formateada, para ver de un vistazo que el sandbox se comporta:
salida normal, stdlib, excepciones, red bloqueada, timeout y cwd.

Uso:
    python scripts/try_run_python.py

No pide permisos (eso vive en el loop del agente): acá invocamos la tool
directo. En una sesión real de `wso`, cada corrida pediría aprobación EXECUTE.
"""

from __future__ import annotations

from wso.tools.code import run_python

CASES: list[tuple[str, str, dict]] = [
    ("Salida básica con print()", "print(2 + 2)", {}),
    ("Stdlib disponible (json, math)",
     "import json, math\nprint(json.dumps({'pi': round(math.pi, 4)}))", {}),
    ("Sin print → aviso de 'sin salida'", "x = 5", {}),
    ("Excepción capturada (traceback en stderr)", "x = 1 / 0", {}),
    ("Imports de red OK, uso de red BLOQUEADO",
     "import urllib.request  # importa bien\n"
     "import socket\n"
     "socket.socket().connect(('1.1.1.1', 80))  # esto sí falla", {}),
    ("cwd del sandbox", "import os\nprint('cwd =', os.getcwd())", {}),
    ("Timeout (loop infinito, corte a 2s)", "while True:\n    pass", {"timeout_s": 2}),
]


def main() -> int:
    for i, (title, code, kwargs) in enumerate(CASES, 1):
        print(f"\n{'=' * 70}\n[{i}] {title}\n{'-' * 70}")
        print("código:")
        for line in code.splitlines():
            print(f"    {line}")
        print("resultado:")
        out = run_python(code, **kwargs)
        for line in out.splitlines():
            print(f"    {line}")
    print(f"\n{'=' * 70}\nListo. Si viste exit=0 en los casos buenos, errores capturados "
          "en los malos,\ny 'Red deshabilitada' + 'TIMEOUT' donde corresponde → run_python anda.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
