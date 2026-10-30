# WSO — instrucciones para agentes que trabajan en este repo

- Python 3.11+, código y comentarios en español rioplatense.
- Tests: `.venv/bin/python -m pytest -q` (sin red ni modelo real).
- Lint: `.venv/bin/ruff check wso tests` (line-length 100).
- Los tests de integración del loop usan `tests/fakes.py` (`make_loop`,
  `ScriptedModel`, `call`, `say`).
- Antes de agregar una tool, leé DESIGN.md §4.3: cada tool ocupa contexto en
  modelos locales.
