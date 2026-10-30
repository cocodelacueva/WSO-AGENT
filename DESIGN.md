# WSO Design Document

Diseño y rationale de **White Suit Operator** v0.4 ("rin").

Complementa al `README.md` (setup y uso). Acá vive el porqué de cada
decisión y el mapa de módulos.

---

## Tabla de contenidos

1. [Visión y principios](#1-visión-y-principios)
2. [Qué cambió en v0.4 y por qué](#2-qué-cambió-en-v04-y-por-qué)
3. [Arquitectura](#3-arquitectura)
4. [Decisiones de diseño](#4-decisiones-de-diseño)
5. [Mapa de módulos](#5-mapa-de-módulos)
6. [Flujo de un turno](#6-flujo-de-un-turno)
7. [Permisos](#7-permisos)
8. [Manejo de contexto](#8-manejo-de-contexto)
9. [Tests](#9-tests)
10. [Cómo extender](#10-cómo-extender)
11. [Roadmap](#11-roadmap)

---

## 1. Visión y principios

WSO es un agente de programación de terminal, en la línea de Claude Code:
recibe pedidos en lenguaje natural y trabaja sobre el proyecto del usuario
(leer, buscar, editar, correr tests). El motor puede ser un modelo local
(Ollama) o uno cloud.

Principios:

1. **Privacidad por default.** Modo local con Ollama; nada sale de la red
   salvo que configures un provider cloud.
2. **Control humano.** Toda escritura y todo comando con efectos pasan por
   aprobación, mostrando el diff o el comando exacto.
3. **Observabilidad.** Texto, thinking y tool calls se ven en vivo; los
   logs JSONL preservan todo para post-mortem.
4. **Una sola fuente de verdad.** Las tools se declaran con `@tool`; su
   JSON Schema sale de la firma de la función.
5. **Funcionar con hardware modesto.** Cada decisión se evalúa contra un
   14B local con 16k–32k de contexto en una GPU de 16 GB.

---

## 2. Qué cambió en v0.4 y por qué

Las versiones 0.1–0.3 eran un agente de propósito general (PPT, Excel, PDF,
browser sobre Sales Navigator). Con Opus funcionaba aceptablemente; con
modelos locales, no. El análisis de los logs de sesión mostró causas
concretas:

| Problema observado | Causa | Cambio en v0.4 |
|---|---|---|
| El modelo "olvidaba" la tarea y escribía en rutas de ejemplo del prompt (`/ruta/que/dio/el/usuario/…`) | System prompt de ~6.400 tokens con `num_ctx=8192`: Ollama recortaba el contexto en silencio | Prompt de ~1.000 tokens; `ContextManager` mantiene el historial bajo la ventana |
| "El modelo no emitió ninguna tool" (respuestas en texto plano) | Protocolo XML propio que el modelo no fue entrenado a usar; turno que solo terminaba con `responder_al_usuario` | Tool calling nativo; una respuesta en texto termina el turno |
| `slides_json: Field required` ×10 | JSON metido como string dentro de un tag XML | Args estructurados con JSON Schema (y se fueron las tools de office) |
| Mala elección de tools | 26 tools siempre cargadas | 6 tools enfocadas en programar |
| 12 errores seguidos por falta de crédito | Errores no reintentables se reintentaban, y además se contaban como "no emitió tool" | `ModelError.retryable`; abortar al primero no reintentable |
| Costo alto con Opus | Sin prompt caching | Caching de tools+system y del historial |

Se descartaron: parser XML, tools de PowerPoint/Excel/PDF/Word, browser
bridge (CDP), `run_python` (lo reemplaza `bash`) y el cliente de Gemini
(se usa vía el endpoint OpenAI-compatible). Todo eso está en la historia
de git (`main`) si alguna vez vuelve a hacer falta.

---

## 3. Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                     Terminal (Rich + prompt_toolkit)              │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│                        AgentLoop (loop.py)                        │
│  ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌─────────┐ ┌────────┐ │
│  │ Context  │ │  Budget  │ │ Permission │ │Registry │ │Renderer│ │
│  │ Manager  │ │ Tracker  │ │  Manager   │ │de tools │ │ (Rich) │ │
│  └──────────┘ └──────────┘ └────────────┘ └─────────┘ └────────┘ │
│                                │                                  │
│                  ModelClient.stream(messages, tools)              │
└────────────────────────────────┬─────────────────────────────────┘
          ┌──────────────────────┼───────────────────────┐
   ┌──────▼──────┐        ┌──────▼──────┐         ┌──────▼──────┐
   │ OllamaClient│        │AnthropicCli.│         │ OpenAIClient│
   │ (local)     │        │ (caching,   │         │ (+OpenRouter│
   │             │        │  thinking)  │         │  Gemini, …) │
   └─────────────┘        └─────────────┘         └─────────────┘
```

Cada cliente traduce el formato neutral (`Message`, `ToolSpec`) al formato
de su API y devuelve eventos `TextDelta` / `ThinkingDelta` / `ResponseDone`.
El loop no sabe con qué provider habla.

---

## 4. Decisiones de diseño

### 4.1 Tool calling nativo

Los modelos actuales (Claude, GPT, Qwen, Llama) están entrenados con el
tool calling de su plataforma. Ollama aplica el template del modelo para
presentar las tools como el modelo las vio en entrenamiento y devuelve las
llamadas ya parseadas. Pelear contra eso con un protocolo propio era la
principal fuente de fallos con modelos locales.

### 4.2 El turno termina cuando el modelo responde sin tools

Igual que Claude Code. Es lo que el modelo hace naturalmente al terminar.
Para preguntar algo, el modelo simplemente pregunta en texto; el usuario
contesta en el turno siguiente. Se eliminaron `responder_al_usuario` y
`preguntar_al_usuario`.

### 4.3 Seis tools, diseñadas para gastar poco contexto

- `read_file` devuelve 250 líneas numeradas por default y avisa cómo
  seguir; recorta líneas larguísimas (minificados).
- `grep`/`glob` primero, leer después: el prompt lo pide y las salidas
  están acotadas (100 resultados).
- `edit_file` por reemplazo exacto: el modelo no reescribe archivos
  enteros (más barato en tokens y menos riesgo de pisar código).
  Tolerancia: si `old_string` no aparece pero su versión sin espacios en
  los bordes es única, se usa (error común de modelos chicos).
- `bash` recorta la salida conservando principio y **final** (donde están
  los errores y el resumen de los tests).

### 4.4 Rescate de tool calls escritas como texto

Algunos modelos locales (qwen2.5-coder) a veces escriben
`{"name": "read_file", "arguments": {...}}` en el texto en vez de usar el
canal nativo. `salvage.py` las detecta si el nombre es una tool conocida y
el texto alrededor es corto (para no confundir una explicación que
muestra JSON con una llamada).

### 4.5 Errores como resultados, no como excepciones al usuario

Una tool que falla (archivo inexistente, args inválidos, edit ambiguo)
devuelve `ERROR: …` como resultado al modelo, con un mensaje que dice qué
hacer ("releé el archivo", "agregá contexto para que sea único"). El
modelo corrige solo.

### 4.6 Errores de modelo: reintentables o no

`ModelError(retryable=…)`. Auth, request inválido, modelo inexistente o
falta de crédito abortan el turno al primer intento; red y sobrecarga se
reintentan hasta 3 veces seguidas.

### 4.7 Prompt caching y thinking en Anthropic

- Breakpoint en el system prompt (cubre tools + system, estables en la
  sesión) y caching automático del último mensaje: cada paso paga solo lo
  nuevo.
- Thinking adaptive con display "summarized": el razonamiento se ve en
  vivo. El contenido nativo de la respuesta (incluidas las firmas de
  thinking) se guarda en `Message.raw` y se devuelve intacto.
- `eager_input_streaming` en las tools: un `write_file` grande llega en
  streaming; la validación la hace `@tool` con Pydantic.

### 4.8 Permisos con aprobación por sesión "a lo Claude Code"

Ver sección 7. La clave de UX: `s` sobre una edición aprueba **todas** las
ediciones del proyecto por la sesión, y sobre un comando aprueba ese
prefijo. Aprobar edición por edición es lo que hace que la gente apague
los permisos.

### 4.9 Estado global cero en el registry

El decorador `@tool` adjunta `_tool_def` a la función; el registry se arma
escaneando módulos. Tests pueden crear registries aislados.

### 4.10 Logging opt-in y no-op por default

`SessionLogger` no toca el disco si `WSO_LOG_ENABLED` no está activo;
abre el archivo recién con el primer evento; si falla la I/O se
auto-deshabilita en vez de tumbar la sesión.

---

## 5. Mapa de módulos

```
wso/
├── main.py                 # entry point: arma dependencias y arranca el REPL
├── config.py               # Settings (pydantic-settings, lee .env)
├── history_store.py        # persistencia opcional del historial
├── session_log.py          # logs JSONL opcionales
├── agent/
│   ├── loop.py             # AgentLoop: turno, pasos, tools, permisos
│   ├── context.py          # ContextManager: mantener el historial en la ventana
│   ├── salvage.py          # rescate de tool calls escritas como texto
│   ├── prompts.py          # system prompt + WSO.md/AGENTS.md/CLAUDE.md + rama git
│   ├── budget.py           # BudgetTracker + prompt de continuación
│   └── model/
│       ├── base.py         # Message, ToolCall, ToolSpec, eventos, ModelClient
│       ├── factory.py      # build_model_client(settings)
│       ├── ollama.py       # Ollama (tools nativas, num_ctx)
│       ├── anthropic.py    # Claude (tools, caching, thinking)
│       └── openai.py       # OpenAI y endpoints compatibles
├── tools/
│   ├── base.py             # @tool, ToolDefinition (validación + JSON Schema)
│   ├── registry.py         # ToolRegistry, load_builtin_tools
│   ├── filesystem.py       # read_file, write_file, edit_file
│   ├── search.py           # glob, grep
│   └── shell.py            # bash
├── permissions/
│   ├── manager.py          # reglas, sesión, always, clasificación de comandos
│   └── prompts.py          # panel de aprobación con diff
└── ui/
    └── console.py          # ConsoleRenderer (Rich)
```

---

## 6. Flujo de un turno

```
usuario: "agregá validación de email en signup y corré los tests"
   │
   ▼
execute_turn
   │  history += user
   │
   └─ loop:
        1. context.fit(...)          ← resumir/descartar si se acerca al límite
        2. model.stream(...)         ← texto/thinking en vivo
        3. ¿tool calls?
             no  → fin del turno (el texto es la respuesta)
             sí  → para cada una:
                     permisos (auto / panel con diff)
                     ejecutar (en un thread, con spinner)
                     history += resultado (tool_call_id)
        4. budget: cada 25 tool calls pregunta si seguir
```

Ejemplo:

```
› agregá validación de email en signup y corré los tests
▶ grep def signup
  ← src/auth.py:42:def signup(email, password):
▶ read_file src/auth.py offset=30 limit=40
  ← 30  …
╭─ ⚠  edit_file ───────────────────────────────────────╮
│ editar src/auth.py                                    │
│ @@ -42,2 +42,4 @@                                     │
│  def signup(email, password):                         │
│ +    if "@" not in email:                             │
│ +        raise ValueError("email inválido")           │
│   y una vez   s sesión (todas las ediciones…)  …      │
╰───────────────────────────────────────────────────────╯
> s
▶ edit_file src/auth.py
▶ bash pytest -q tests/test_auth.py
  ← [exit 0, 1.2s] 8 passed
Listo: signup ahora rechaza emails sin "@". Los 8 tests de auth pasan.
contexto ~4.1k/16.4k tokens
```

---

## 7. Permisos

| Categoría | Tools | Auto-aprobado | `s` (sesión) | `a` (siempre) |
|---|---|---|---|---|
| READ | read_file, glob, grep | dentro del proyecto | esa carpeta (fuera del proyecto) | `<carpeta>/*` |
| WRITE | edit_file, write_file | nunca | todas las ediciones del proyecto (fuera: ese archivo) | `<carpeta>/*` |
| EXECUTE | bash | comandos de solo lectura | mismo prefijo | mismo prefijo |

**Comandos de solo lectura** (`is_readonly_command`): `ls`, `cat`, `head`,
`tail`, `wc`, `rg`, `grep`, `find` sin `-exec`/`-delete`, `git status/diff/
log/show/blame`, etc. Se permiten pipes entre ellos; nunca `;`, `&`,
redirecciones ni sustitución. Paths explícitos (`/x`, `~/x`, `../x`) tienen
que estar dentro del proyecto.

**Prefijos** (`command_prefix`): `pytest -x` → `pytest`; `git commit -m x`
→ `git commit`; `python -m pytest` → `python -m pytest`. Un prefijo nunca
matchea un comando con metacaracteres de shell.

`n` corta el turno (el modelo recibe "rechazado, esperá instrucciones");
texto libre rechaza y le pasa la indicación al modelo, que sigue.

---

## 8. Manejo de contexto

El problema: Ollama recorta en silencio el prompt que excede `num_ctx`, y
con eso se pierden el system prompt o el pedido del usuario.

`ContextManager.fit` corre antes de cada llamada. Estima tokens por
caracteres (3 caracteres/token, conservador para código) y, solo si se
supera el 75% de la ventana útil (`num_ctx` − reserva para la respuesta):

1. Resume resultados de tools viejos (conserva los 4 más recientes) a una
   línea: `[omitido para ahorrar contexto: resultado de read_file de 9.000
   caracteres… volvé a ejecutar la tool si lo necesitás]`.
2. Si no alcanza, descarta turnos completos desde el principio (nunca el
   actual; siempre corta en un mensaje del usuario para no dejar tool
   calls sin resultado).
3. Si el turno actual solo no entra, resume también sus resultados salvo
   el último.

Con modelos cloud (ventanas de 128k–1M) prácticamente nunca dispara, así
que el historial queda append-only: es lo que necesitan el prompt caching
y la validación de thinking de Claude.

No hay compactación por resumen con LLM (todavía): con un 14B local
cuesta una llamada lenta y el resumen es de calidad dudosa. Ver roadmap.

---

## 9. Tests

`pytest -q` — ~280 tests, sin red ni modelo real:

| Archivo | Qué cubre |
|---|---|
| `test_agent_loop.py` | Turnos completos con `ScriptedModel`: fin por texto, tools, permisos (y/n/s/feedback), errores de modelo, truncado, rescate, interrupción, budget, contexto, logs |
| `test_agent_context.py` | Resumen, descarte de turnos, nunca el turno actual |
| `test_agent_salvage.py` | Rescate de tool calls en texto y falsos positivos |
| `test_model_formats.py` | Conversión a Anthropic/OpenAI/Ollama, serialización |
| `test_tools_*.py` | read/write/edit, glob/grep (con rg y fallback Python), bash (timeout, recorte) |
| `test_permissions_*.py` | Reglas, sesión, always, clasificación de comandos, diff del panel |
| `test_history_store.py`, `test_session_log.py`, `test_agent_budget.py`, `test_ui_console.py`, `test_agent_prompts.py` | El resto |

`tests/fakes.py` tiene el `ScriptedModel` y `make_loop` para escribir
tests de integración del loop en pocas líneas.

---

## 10. Cómo extender

### Agregar una tool

```python
# wso/tools/mi_modulo.py
from wso.tools.base import PermissionCategory, tool

@tool(
    name="run_tests",
    category=PermissionCategory.EXECUTE,
    description="Corre los tests del proyecto y devuelve el resumen.",
    args_schema={"path": "archivo o carpeta de tests (vacío = todos)"},
)
def run_tests(path: str = "") -> str:
    ...
```

Registrala en `load_builtin_tools()` (`tools/registry.py`). El JSON Schema
sale de la firma; la validación de args la hace Pydantic. Antes de agregar
tools pensá en el modelo local: cada tool ocupa contexto y es una opción
más entre las que elegir.

### Agregar un provider

Implementá `ModelClient.stream(messages, tools)` en
`agent/model/<provider>.py` emitiendo `TextDelta`/`ThinkingDelta` y un
`ResponseDone` final; agregá el caso en `factory.py`. Si el provider es
OpenAI-compatible, no hace falta: `WSO_OPENAI_BASE_URL`.

### Instrucciones por proyecto

Un `WSO.md` en la raíz del proyecto se agrega al system prompt (máx. 6.000
caracteres). Ideal para: cómo correr los tests, convenciones, carpetas a
no tocar.

---

## 11. Roadmap

**v0.4 "rin" (este branch):**
- [x] Tool calling nativo en Ollama, Anthropic y OpenAI-compatibles
- [x] Fin de turno por respuesta en texto
- [x] 6 tools de programación con salidas acotadas
- [x] Permisos con diff, aprobación por sesión y comandos de solo lectura
- [x] Manejo de contexto para ventanas chicas
- [x] Prompt caching + thinking en Anthropic
- [x] Rescate de tool calls en texto (modelos locales)
- [ ] Pruebas reales con modelos locales (qwen2.5-coder:14b, qwen3:14b, …)
      y ajuste del prompt según lo que se vea en los logs

**Siguiente:**
- [ ] Compactación con resumen por LLM (`/compact`) para sesiones largas
- [ ] Modo no interactivo: `wso -p "tarea"` para scripts/CI
- [ ] Editar args antes de aprobar
- [ ] Tool `todo` para que el modelo planifique tareas largas
- [ ] Subagentes de exploración con contexto propio (cuando valga la pena
      con modelos locales)
