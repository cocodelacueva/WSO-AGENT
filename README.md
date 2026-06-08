# White Suit Operator — WSO

Agente de terminal para asistir tareas del estudio. Diseñado para correr
local (Ollama) o contra modelos cloud (Anthropic, OpenAI, Google) usando
la misma interfaz.

**Versión:** 0.2.0 (PPT, PDF, Excel)
**Versión:** 0.1.0 (Local Agent Prototype)

---

## Setup

Para correr el programa se necesita: Python 3.11.

```bash
# 1. Instalar dependencias del sistema
ollama pull qwen2.5-coder:32b

# 2. Instalar dependencias Python
pip install -r requirements.txt
# o en modo editable:
pip install -e ".[all]"

# 3. Configurar entorno
cp .env.example .env
# editá .env según tu setup

# 4. Correr el agente
wso
# o:
python -m wso.main
```

### Usar maquina virtual de python:

**Mac/linux:**
```bash
cd /Users/coco/Documents/DESAROLLO/wso-ai-harness
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
wso
```

**Requerimientos:**
Se necesita un PYthon 3.11 o para arriba.
**Mac:**
brew install pyenv
pyenv install 3.12
pyenv local 3.12   # solo en este proyecto

Y luego se crear la venv con python -m venv .venv

**WINDOWS:**

```bash
cd C:\Users\coco\Documents\DESAROLLO\wso-ai-harness
python -m venv .venv
Mac: source .venv/bin/activate
windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
wso
```

## Modelos sugeridos

qwen2.5-coder:32b     # código, PPTs, Excel, scripts / me resulta muy pesado
qwen2.5-coder:14b     # código, PPTs, Excel, scripts / Mas liviano que el otro
qwen2.5:14b           # conversación, redacción, estrategia
qwen2.5:7b            # tareas rápidas (opcional)

## Configuración

WSO se configura con un archivo `.env` en la raíz del proyecto.
La configuración mínima para correr local con Ollama:

```env
WSO_MODE=local
WSO_LOCAL_URL=http://localhost:11434     # o IP de la PC con Ollama
WSO_LOCAL_MODEL=qwen2.5-coder:14b
WSO_LOCAL_NUM_CTX=16384                  # tamaño del contexto (KV cache)
```

`WSO_LOCAL_NUM_CTX` controla cuánta VRAM consume el KV cache (escala
lineal con el contexto). El default es **16384**: da aire para tareas
con lecturas grandes (resumir un PDF, armar un deck desde un docx). Con
num_ctx chico (4096-8192) una sola lectura grande desaloja del contexto
el system prompt y lo que pediste, y el modelo "olvida" la tarea a mitad
de camino. Con un 14B en GPU de 16GB, 16384 entra cómodo (~13GB total).
Si usás un 32B o GPU chica, bajalo a 8192/4096 para no spillear a CPU
(mirá `ollama ps`: querés ver `100% GPU`).

Complementariamente, las tools de lectura (`read_pdf`, `read_docx`,
`read_pptx`) aceptan un `max_chars` (default 16000) que acota cada
lectura a un tamaño predecible y avisa si el documento sigue. `read_pdf`
además acepta `start_page` para leer PDFs largos por tramos.

### Cloud providers

WSO soporta los tres grandes providers cloud usando la misma
interfaz. Solo cambia el `.env` y reiniciás `wso`.

**Google Gemini** (free tier generoso, recomendado para empezar):

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=google
WSO_CLOUD_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=tu-key
```

```bash
pip install "google-generativeai>=0.8"
```

**Anthropic Claude** (requiere prepay desde console.anthropic.com):

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=anthropic
WSO_CLOUD_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=sk-ant-...
```

```bash
pip install "anthropic>=0.40"
```

**OpenAI GPT:**

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=openai
WSO_CLOUD_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

```bash
pip install "openai>=1.50"
```

### Tools de Office y documentos

Para generar/editar `.pptx`/`.xlsx` y leer `.pdf`/`.docx`, instalá la
extra opcional:

```bash
pip install -e ".[office]"
# o: pip install python-pptx openpyxl pypdf python-docx
```

Sin esta extra, las tools siguen registradas pero fallan al invocarse
con un mensaje guía pidiendo instalar la dep correspondiente. Las demás
tools del agente (filesystem, flow) no se ven afectadas.

Tools disponibles:

- **PPTX**: `generate_pptx`, `read_pptx`, `edit_pptx_slide`,
  `generate_pptx_from_template`.
- **XLSX**: `generate_xlsx`, `read_xlsx`, `edit_xlsx_cell`,
  `append_xlsx_rows`.
- **Lectura de documentos**: `read_pdf` (extrae texto por página, con
  `start_page`/`max_chars`) y `read_docx` (párrafos, headings y tablas).
  Útiles para resumir, traducir o tomar contenido para un deck/Excel.

Las tools de generación de PPTX toleran variaciones del JSON que emiten
los modelos chicos: `layout` opcional (default `content`), `content`/
`text` se coercionan a `bullets`, y el arg `slides` funciona como alias
de `slides_json`. Esto reduce los errores de validación en vueltas con
modelos locales.

### Browser bridge (v0.3, en progreso)

WSO puede manejar tu **Chrome real** para operar apps web sin API
(LinkedIn, Sales Navigator, dashboards internos). No arranca un Chrome
propio: se attachea por CDP a uno que vos iniciás, reutilizando tus
cookies y sesiones logueadas. LinkedIn ve tu navegador, no un bot.

Instalá la extra y el binario de Playwright:

```bash
pip install -e ".[browser]"
# (para attach puro por CDP no hace falta `playwright install`)
```

Arrancá Chrome con remote debugging (perfil aislado, recomendado):

```bash
./scripts/launch-chrome-cdp.sh            # Mac/Linux
# Windows: .\scripts\launch-chrome-cdp.ps1
```

La primera vez logueate en los sitios que vayas a usar dentro de esa
ventana aislada (`~/.wso-chrome`); la sesión queda persistida. Con
`--default` usás tu perfil real (más cómodo, menos seguro; cerrá las
otras ventanas de Chrome antes).

Configurá el endpoint (default ya alineado con el script):

```env
WSO_BROWSER_CDP_URL=http://localhost:9222
```

Tools disponibles: `browser_open_tab`, `browser_navigate`,
`browser_close_tab`, `browser_read_page`, `browser_screenshot`,
`browser_click`, `browser_type`, `browser_wait_for`.

Permisos: las acciones de browser (navegar, click, type) son categoría
`BROWSER` y **nunca se auto-aprueban por default**. Aprobar una
navegación "por sesión" (`s`) habilita futuras navegaciones al mismo
dominio. Nada de browser se persiste entre sesiones.

Antes de codear esto validamos el attach con `scripts/spike_cdp_attach.py`
(lee tus tabs y el texto de Sales Navigator sin tocar nada).

### Logging estructurado de sesiones

Para activar logging de cada sesión, configurá en `.env`:

```env
WSO_LOG_ENABLED=true
```

Cada corrida del REPL escribe un archivo `logs/session_YYYYMMDDTHHMMSS.jsonl`
con un evento por línea (turn_start, model_response, tool_call, permission,
observation, error, etc). Útil para:

- Debug post-mortem ("¿por qué el agente hizo X ayer?").
- Auditoría de acciones aprobadas.
- Comparar prompts/modelos.
- Replay de sesiones reales como fixtures de test.

Los `.jsonl` están en `.gitignore` por default, no se commitean. Si no
activás el flag, el logger queda como no-op y no toca el disco.

**OpenRouter** (acceso a Claude/GPT/Gemini/etc con una sola key,
sin minimum deposit alto):

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=openai
WSO_CLOUD_MODEL=anthropic/claude-sonnet-4.5
WSO_OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
```

OpenRouter usa formato OpenAI-compatible, así que se conecta vía el
provider `openai` con un `base_url` redirigido. Cambiando solo el
`WSO_CLOUD_MODEL` podés saltar entre Claude, GPT, Gemini, Mistral,
Llama, etc. con la misma key.

## Arquitectura

WSO es un harness agéntico conversacional con cuatro principios de diseño:

1. **Patrón híbrido** — tools tipadas para operaciones comunes,
   `run_python` como escape hatch para casos no previstos (v3).
2. **Loop con elicitación explícita** — el turno solo termina cuando el
   modelo invoca `responder_al_usuario` o `preguntar_al_usuario`.
3. **Permisos granulares** — todas las acciones de escritura/borrado
   piden aprobación; lectura solo dentro de un whitelist.
4. **Streaming visible** — el "thinking" del modelo, las tool calls y
   las observaciones se renderizan en vivo en la terminal.

## Estructura del proyecto

```
wso/                   # código del agente
├── main.py            # entry point CLI
├── config.py          # settings (lee .env)
├── agent/             # loop, parser XML, prompts, budget, clientes de modelo
├── tools/             # tools del agente (read_file, write_file, etc)
├── permissions/       # sistema de aprobación con sticky permissions
└── ui/                # rendering Rich

workspace/             # área de trabajo del agente (sandboxed)
├── context/           # info de devs, proyectos, briefs
├── automations/       # scripts custom invocables vía run_python (v3)
└── output/            # entregables generados

config/                # settings.toml y permissions.toml persistentes
logs/                  # audit trail de sesiones
```

## Roadmap

**v1 (cerrado):**
- [x] Loop agéntico con budget y continuación
- [x] Parser XML streaming tolerante
- [x] Tools de filesystem (read, write, delete, list)
- [x] Tools de control de flujo (responder, preguntar)
- [x] Sistema de permisos completo
- [x] Cliente Ollama (con `num_ctx` configurable)
- [x] Clientes Anthropic, OpenAI, Google
- [x] Soporte OpenAI-compatible endpoints (OpenRouter, Together.ai, etc)
- [x] UI terminal con Rich
- [x] 401 tests pasando

**v2 (cerrado):**
- [x] Tools de PowerPoint (`generate_pptx`, `read_pptx`, `edit_pptx_slide`, `generate_pptx_from_template`)
- [x] Tools de Excel (`generate_xlsx`, `read_xlsx`, `edit_xlsx_cell`, `append_xlsx_rows`)
- [x] Tools de lectura de documentos (`read_pdf`, `read_docx`)
- [x] Logging estructurado a `logs/session_*.jsonl` (opt-in vía `WSO_LOG_ENABLED=true`)
- [x] Robustez post-prueba 2: schema PPTX tolerante (layout opcional,
      `content`→`bullets`, alias `slides`), guardrails de rutas/archivos en
      el system prompt, `num_ctx` default 16384 y `max_chars` en lecturas

**v3 (en progreso):**
- [x] **Spike de validación CDP** — confirmado que Playwright + CDP attach
      lee Sales Navigator real con la sesión logueada y sin detección de
      automation (`navigator.webdriver=false`). Ver `scripts/`.
- [x] **Browser bridge mínimo** — el agente maneja tu Chrome real
      (vía Playwright + CDP attach), reutilizando tus sesiones logueadas.
      8 tools (`browser_open_tab`, `browser_navigate`, `browser_close_tab`,
      `browser_read_page`, `browser_screenshot`, `browser_click`,
      `browser_type`, `browser_wait_for`), categoría de permiso `BROWSER`
      con sticky por dominio. Reemplaza el approach LinkedIn-RSS (no viable:
      LinkedIn bloquea y Sales Navigator no expone RSS). Ver DESIGN.md,
      apéndice "Browser bridge".
- [ ] E2E manual contra LinkedIn/Sales Navigator y endurecimiento
      (stealth, delays, detección de session expiry).
- [ ] `run_python` con sandbox (subprocess aislado + cwd limitado + timeout)
- [ ] Persistencia de historial entre sesiones
- [ ] Memoria de largo plazo (RAG sobre `/context`)
- [ ] Tools async (HTTP, base de datos)

## Notas de diseño

> "Este sistema debe priorizar la privacidad absoluta. Ningún dato de
> White Suit Studio sale de la red local salvo configuración explícita
> a un provider cloud."

Ver `blueprint.md` para el documento de diseño original.
