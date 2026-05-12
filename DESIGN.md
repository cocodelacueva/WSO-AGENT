# WSO Design Document

Diseño y rationale de **White Suit Operator** v0.1.0.

Este documento es complementario al `README.md` (que cubre setup y uso).
Acá vive la justificación arquitectónica de cada decisión y el mapa de
qué hace cada módulo.

---

## Tabla de contenidos

1. [Visión y principios](#1-visión-y-principios)
2. [Arquitectura general](#2-arquitectura-general)
3. [Decisiones de diseño](#3-decisiones-de-diseño)
4. [Mapa de módulos](#4-mapa-de-módulos)
5. [Flujo de un turno](#5-flujo-de-un-turno)
6. [Sistema de permisos](#6-sistema-de-permisos)
7. [Parser XML streaming](#7-parser-xml-streaming)
8. [Tests](#8-tests)
9. [Cómo extender](#9-cómo-extender)
10. [Roadmap](#10-roadmap)

---

## 1. Visión y principios

WSO es un **harness agéntico** local de terminal: un programa que recibe
instrucciones en lenguaje natural y las ejecuta sobre el sistema de
archivos del usuario, usando un LLM (Ollama por defecto, cloud opcional)
como motor de razonamiento.

Cuatro principios guían el diseño:

1. **Privacidad por default.** Modo local con Ollama; ningún dato sale
   de la máquina salvo configuración explícita.
2. **Control humano.** Toda escritura/borrado pasa por aprobación.
   Los permisos son sticky (sesión o persistentes).
3. **Observabilidad.** El razonamiento del modelo, las tool calls y
   las observaciones se muestran en vivo en la terminal.
4. **Una sola fuente de verdad.** Las tools se declaran con un
   decorador `@tool`; el system prompt se autogenera. Si renombrás
   un arg, todo se actualiza solo.

---

## 2. Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│                          Terminal (Rich)                          │
└─────────────────────────────┬───────────────────────────────────┘
                              │ user input / display
┌─────────────────────────────▼───────────────────────────────────┐
│                        AgentLoop (loop.py)                      │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  Budget   │  │ Renderer │  │  Permission  │  │ Registry   │ │
│  │  Tracker  │  │ (Rich)   │  │   Manager    │  │ de tools   │ │
│  └───────────┘  └──────────┘  └──────────────┘  └────────────┘ │
│         │             │              │                  │        │
│         └─────────────┴──────────────┴──────────────────┘        │
│                              │                                   │
│  ┌────────────────────┐  ┌───────────────────┐                 │
│  │ StreamingXMLParser │  │   ModelClient     │                 │
│  │  (state machine)   │  │   (ABC + factory) │                 │
│  └────────────────────┘  └────────┬──────────┘                 │
└──────────────────────────────────┬─┴───────────────────────────┘
                                   │
     ┌──────────────┬──────────────┬──────────────┬──────────────┐
     │              │              │              │              │
┌────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
│ Ollama   │ │ Anthropic   │ │ OpenAI     │ │ Google     │ │ OpenRouter │
│ Client   │ │ Client      │ │ Client     │ │ Client     │ │ (vía       │
│ (local)  │ │ (Claude)    │ │ (GPT)      │ │ (Gemini)   │ │ OpenAI     │
└──────────┘ └─────────────┘ └────────────┘ └────────────┘ │ compatible)│
                                                            └────────────┘
```

El loop es el componente central. Recibe input del usuario, llama al
modelo en streaming, parsea la salida con un parser tolerante, valida
permisos, ejecuta tools, y renderiza todo en vivo.

---

## 3. Decisiones de diseño

Cada decisión arquitectónica se discutió antes de implementarse. Las
principales:

### 3.1 Paradigma de ejecución: híbrido (Patrón C)

Tools tipadas para operaciones comunes (read/write/delete/list, control
de flujo) con un escape hatch `run_python` previsto para v0.3. La razón:
los casos repetitivos (PPT, Excel, LinkedIn) merecen tools tipadas para
ser predecibles y auditables; los casos raros pueden caer al sandbox de
Python para no codear una tool por cada combinación. Diferimos el sandbox
a v0.3 porque hacerlo bien (subprocess + resource limits + filesystem
isolation) merece su propia iteración de diseño.

### 3.2 Conversacional con elicitación explícita (Modelo 3)

El turno termina solo cuando el modelo invoca `responder_al_usuario` o
`preguntar_al_usuario`. Cualquier otra cosa (texto plano, otras tools)
deja el loop activo.

Por qué: separa "respuesta final" de "trabajo intermedio" en la UI, y
le permite al modelo pedir clarificación deliberadamente en lugar de
inventar suposiciones.

### 3.3 Contrato XML con tags hijos

```xml
<thinking>razonamiento</thinking>
<tool name="read_file">
  <path>/Users/coco/notes.md</path>
</tool>
```

Por qué XML y no JSON function calling: los modelos locales tienen
mejor adherencia a XML (entrenados sobre datasets que incluyen Claude-
style prompts), y un parser tolerante puede streaming-detect tags
incompletos. El JSON puro de Ollama function calling es frágil con
modelos chicos.

### 3.4 Una tool por respuesta

El modelo emite `<thinking>` (1 o más bloques) + un solo `<tool>`. El
loop ejecuta, devuelve `<observation>`, y vuelve a llamar al modelo.
Más predecible que paralelización con modelos locales.

### 3.5 Streaming visible (estilo ReAct)

Cada chunk del thinking se renderiza en vivo en italic gris. El usuario
ve el razonamiento mientras se forma. Cuando llega un `<tool>`, se
muestra con un símbolo `▶` cyan; la observación con `←`.

### 3.6 Permisos por categoría con visibilidad de la acción

Cada tool tiene una `PermissionCategory` (read/write/delete/network/
execute/flow). El prompt de aprobación muestra la acción específica
(no solo la categoría) para que el usuario sepa qué está aprobando.

### 3.7 Sticky permissions con asimetría session/always

- **Session-allow** usa exact-match de args (predecible).
- **Always-allow** usa pattern matching sobre el path (más útil
  porque cubre múltiples archivos del mismo dir).

Esa asimetría refleja que `s` es una decisión rápida sobre una llamada
concreta, mientras que `a` es deliberada y merece scope amplio.

### 3.8 Whitelist read pre-aprobado

`workspace/context`, `workspace/automations`, `workspace/output` y
`cwd` están auto-aprobados para lectura. Cualquier otro path pide
aprobación per-session.

### 3.9 Budget como mecanismo de seguridad, no de control

10 acciones por turno. El loop NO termina por budget — termina por
elicitación explícita. El budget solo dispara si el modelo entra en
loop. Cuando se agota, mostramos un panel con history (✓/✗) y el
usuario decide: continuar 10 más, abortar, o abortar con feedback.

### 3.10 Errores como excepciones, no como strings

Las tools propagan `FileNotFoundError`, `IsADirectoryError`, etc. El
loop las captura y las formatea como observation. Esto separa
responsabilidades: el handler ejecuta, el loop decide cómo formatear
el error como observation. También hace los tests más limpios.

### 3.11 Estado global cero en el registry

El decorador `@tool` adjunta `_tool_def` a la función como atributo;
no toca ningún global. El `ToolRegistry` se construye escaneando
módulos. Tests pueden crear módulos sintéticos y registries aislados
sin contaminarse entre sí.

### 3.12 TOML para permisos persistentes

Usamos `tomllib` (3.11+) o `tomli` para lectura. Para escritura,
escribimos TOML manualmente (formato simple) y evitamos la dependencia
adicional `tomli_w`.

### 3.13 Abstracción de modelo: cuatro providers detrás de la misma interfaz

`ModelClient` ABC con factory que despacha por `WSO_MODE` y
`WSO_CLOUD_PROVIDER`. v1 implementa los cuatro:

- **`OllamaClient`** — local, con `num_ctx` configurable para ajustar
  el KV cache según la VRAM disponible.
- **`AnthropicClient`** — Claude. Maneja `system` como param separado
  (convención de Anthropic) y mergea mensajes consecutivos del mismo
  rol para evitar errores de la API.
- **`OpenAIClient`** — GPT, con `base_url` opcional. Esto habilita
  endpoints OpenAI-compatible: OpenRouter, Together.ai, Groq, Azure
  OpenAI, vLLM local. Una sola clase cubre muchísimos providers.
- **`GoogleClient`** — Gemini. Convierte `system` a `system_instruction`
  (param separado) y `assistant` a `model` (convención de Gemini).

El loop solo conoce la interfaz `ModelClient.stream_chat()`. Cambiar
de provider es solo cambiar `.env`; el código del agente no se entera.

### 3.14 KV cache configurable en local

Para Ollama, `num_ctx` controla el tamaño del KV cache (memoria que
escala linealmente con el contexto). Modelos grandes (~30B) con
contexto default de 16k consumen >40GB total y no caben en GPUs
medianas (16GB). Exponemos `WSO_LOCAL_NUM_CTX` con default 8192 para
que el usuario lo ajuste según su hardware. Esto es la diferencia
entre `100% GPU` y `50/50 CPU/GPU` en `ollama ps`.

### 3.16 Logging estructurado opt-in con no-op default

El `SessionLogger` (`wso/session_log.py`) es **opt-in vía
`WSO_LOG_ENABLED=true`** y opera como **no-op** si está deshabilitado.
Decisiones internas:

- **No-op real, no flag interno.** Si `log_dir is None`, ningún archivo
  se abre, ningún disco se toca, ningún ciclo de CPU se gasta. El loop
  llama a `.log()` siempre, el método chequea `enabled` y retorna early.
- **Lazy file open.** El archivo se crea recién cuando llega el primer
  evento. Si el usuario abre y cierra `wso` sin tipear nada, no queda
  basura en `logs/`.
- **Tolerante a I/O errors.** Si escribir falla (disco lleno, permisos,
  etc.), el logger se auto-deshabilita en lugar de tirar la sesión. El
  logging es accesorio, no debería poder romper el flow.
- **JSONL canónico.** Un evento por línea, válido JSON, terminado en
  `\n`. Parseable con `for line in f: json.loads(line)`, truncable con
  `head` / `tail`, rotable sin parser propio.
- **Dependencias mínimas.** Solo stdlib (`json`, `datetime`, `pathlib`,
  `contextlib`). No agrega superficie de dep al proyecto.

Esto cierra el principio 3 (Observabilidad) con un canal persistente,
complementando al streaming visible en terminal: la terminal muestra
en vivo, el JSONL preserva post-mortem.

### 3.15 Args complejos: JSON dentro de un arg string

Las tools de Office (v0.2) necesitan recibir estructuras anidadas: una
lista de slides, cada uno con su layout, título, bullets, notas, etc.
El parser XML de v1 no soporta tags anidados en el body de `<tool>`
(ver sección 7 — limitación conocida). Tenemos dos caminos:

1. Codear v0.2 del parser con XML anidado real (CDATA / entity escaping).
2. **Workaround**: el arg es un string que contiene JSON. El modelo emite
   el JSON como texto plano dentro de `<slides_json>...</slides_json>`,
   el handler parsea con `json.loads` y valida con Pydantic.

Elegimos (2) para v0.2 porque:

- No requiere tocar el parser (riesgo de regresiones en algo crítico).
- El JSON es un formato que los modelos ya conocen bien y emiten correctamente.
- Pydantic con discriminated unions valida cleanly y produce errores legibles
  (campo `layout` decide la forma exacta del slide).
- Coherente con `preguntar_al_usuario`, que ya devuelve JSON internamente.

Trade-off explícito: el modelo ve un string opaco en el prompt en lugar
de campos individuales. Mitigado con un ejemplo claro en `args_schema`
del decorador. El XML anidado real queda en el roadmap para v0.3 si
algún caso lo necesita.

---

## 4. Mapa de módulos

```
wso/
├── main.py                       # entry point CLI; wiring final
├── config.py                     # Pydantic Settings; lee .env
├── agent/
│   ├── loop.py                   # AgentLoop (orquestador)
│   ├── parser.py                 # StreamingXMLParser
│   ├── prompts.py                # build_system_prompt, load_context_files
│   ├── budget.py                 # BudgetTracker, ask_continuation
│   └── model/
│       ├── base.py               # ModelClient ABC, Message
│       ├── factory.py            # build_model_client(settings)
│       ├── ollama.py             # OllamaClient (local, num_ctx configurable)
│       ├── anthropic.py          # AnthropicClient (Claude)
│       ├── openai.py             # OpenAIClient (GPT + OpenAI-compatible)
│       └── google.py             # GoogleClient (Gemini)
├── tools/
│   ├── base.py                   # @tool decorador, ToolDefinition, ToolValidationError
│   ├── registry.py               # ToolRegistry, load_builtin_tools
│   ├── filesystem.py             # read_file, write_file, delete_file, list_directory
│   ├── flow.py                   # responder_al_usuario, preguntar_al_usuario
│   ├── pptx.py                   # generate_pptx, read_pptx, edit_pptx_slide, generate_pptx_from_template
│   ├── pptx_schemas.py           # Pydantic models de slides (discriminated union)
│   ├── xlsx.py                   # generate_xlsx, read_xlsx, edit_xlsx_cell, append_xlsx_rows
│   ├── xlsx_schemas.py           # Pydantic models de sheets/workbook
│   └── code.py                   # run_python (stub — implementación en v0.3)
├── permissions/
│   ├── manager.py                # PermissionManager + AlwaysAllowRule
│   └── prompts.py                # ask_approval, parse_approval_input
├── ui/
│   └── console.py                # ConsoleRenderer (Rich wrapper)
└── session_log.py                # SessionLogger (JSONL estructurado, opt-in)
```

### Responsabilidades por módulo

**`config.py`** — Única fuente de verdad para configuración. Lee `.env`
con `pydantic-settings`. Provee paths derivados (`workspace_dir`,
`context_dir`, `permissions_file`, etc.) como properties. Ningún otro
módulo lee `os.environ` directamente.

**`agent/loop.py`** — El orquestador. `run_repl()` es el loop
conversacional infinito. `execute_turn(input)` corre un turno entero
hasta que un flow tool lo termine. `_execute_step()` hace una iteración
(model → parser → permissions → tool → observation). Métodos privados
encapsulan cada paso del flujo.

**`agent/parser.py`** — Máquina de estados que consume chunks del
modelo y emite eventos: `ThinkingChunk`, `ThinkingEnd`,
`ToolCallComplete`, `PlainTextChunk`, `ParseError`. Maneja partials
con hold-back conservador del último `<` ambiguo.

**`agent/prompts.py`** — `build_system_prompt(registry, context)` arma
un prompt de ~4400 chars con identidad, reglas, contrato XML, tools
auto-documentadas (vía `registry.to_prompt_section()`), few-shot
example, y contexto del estudio.

**`agent/budget.py`** — `BudgetTracker` con history de `StepRecord`
(✓/✗). `ask_continuation()` muestra un panel con resumen y lee la
decisión del usuario.

**`agent/model/`** — Capa de abstracción. `ModelClient` ABC con
`stream_chat(messages) -> AsyncIterator[str]`. Cuatro implementaciones
concretas: `OllamaClient` (local, con `num_ctx` configurable),
`AnthropicClient` (Claude, system prompt separado, merge de mensajes
consecutivos), `OpenAIClient` (GPT y endpoints OpenAI-compatible vía
`base_url`), `GoogleClient` (Gemini, formato role='model' para
respuestas). El factory despacha según `WSO_MODE` y
`WSO_CLOUD_PROVIDER`.

**`tools/base.py`** — El corazón del sistema de tools:
- `PermissionCategory` enum
- `ToolDefinition` con `validate_and_call()` y `to_prompt_section()`
- `@tool` decorador que infiere args desde la signature, valida
  schema contra firma, construye un Pydantic model dinámico para
  validación runtime

**`tools/registry.py`** — `ToolRegistry` que escanea módulos buscando
funciones con `_tool_def` adjunto. `load_builtin_tools()` importa
`filesystem`, `flow`, `pptx` y `xlsx`, y devuelve un registry poblado
con 14 tools.

**`tools/pptx.py`** — Cuatro tools declarativas sobre `python-pptx`:
`generate_pptx` (crear desde JSON), `read_pptx` (extraer texto y notas),
`edit_pptx_slide` (modificación puntual in-place), y
`generate_pptx_from_template` (reusa branding de un template). La dep
`python-pptx` se importa lazy en `_require_pptx()` — el módulo se importa
y se registran las tools sin tenerla instalada; solo falla al invocarse
con un mensaje guía pidiendo `pip install -e ".[office]"`.

**`tools/pptx_schemas.py`** — Modelos Pydantic de cada layout de slide
(`TitleSlide`, `ContentSlide`, `SectionHeaderSlide`, `TwoContentSlide`,
`ImageSlide`, `BlankSlide`) unidos en un `Annotated[Union[...],
Field(discriminator="layout")]`. Permite que un solo JSON valide a la
variante correcta y devuelva errores claros si la forma no matchea
ninguna.

**`tools/xlsx.py`** — Cuatro tools sobre `openpyxl`: `generate_xlsx`
(crear desde JSON de sheets), `read_xlsx` (extraer datos con
`max_rows` opcional), `edit_xlsx_cell` (modificar celda puntual con
notación A1), y `append_xlsx_rows` (agregar filas al final, útil para
tracking incremental). Soporta múltiples sheets, headers en bold,
y fórmulas (cualquier string que empiece con `=`). Lazy import vía
`_require_openpyxl()` con el mismo patrón que pptx.

**`tools/xlsx_schemas.py`** — Modelos `Sheet` (name, headers, rows) y
`Workbook` (lista de sheets). Las celdas son `bool | int | float | str
| None` (los tipos nativos de JSON). Sin discriminated union acá: el
shape de Sheet es uniforme y no necesita ramificación por tipo.

**`permissions/manager.py`** — `PermissionManager` con check, remember,
y persistencia. La regla de despacho es:
1. FLOW → AUTO_APPROVED
2. READ + path en whitelist → AUTO_APPROVED
3. Always-allow rule matches → AUTO_APPROVED
4. Session-allow exact match → AUTO_APPROVED
5. Default → NEEDS_APPROVAL

**`permissions/prompts.py`** — UI de aprobación. `parse_approval_input`
separado de `ask_approval` para testeo sin I/O. Códigos
`y/s/a/n` case-insensitive; cualquier otro texto se trata como feedback.

**`ui/console.py`** — `ConsoleRenderer` con métodos semánticos
(`render_thinking_chunk`, `render_tool_call`, `render_observation`,
etc). Centraliza Rich. Cambiar el estilo no requiere tocar lógica.

**`session_log.py`** — `SessionLogger` que serializa eventos del agente
a JSONL en `logs/session_{timestamp}.jsonl`. Opt-in vía
`WSO_LOG_ENABLED=true`. Si está deshabilitado (default), todas las
llamadas a `.log()` son no-op y no se toca el disco. Lazy file open
(no se crean archivos vacíos), tolerante a errores de I/O (si falla
escribir se auto-deshabilita en vez de tumbar la sesión), encoding
UTF-8, eventos one-per-line con timestamp ISO 8601 UTC. El loop lo
inyecta como dependencia y lo llama en puntos clave: session_start,
turn_start, model_response, tool_call, permission, observation, error,
turn_end, session_end.

---

## 5. Flujo de un turno

Un turno completo desde input del usuario hasta respuesta final:

```
USUARIO escribe: "leeme el archivo notes.md de context/"
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ AgentLoop.execute_turn(input)                                   │
│                                                                  │
│  budget.start_turn(goal=input)                                  │
│  history.append(user_msg)                                       │
│                                                                  │
│  while True:                                                    │
│    if budget.is_exhausted():                                    │
│      response = ask_continuation()                              │
│      if response.decision == 'abort': return                    │
│      else: budget.extend(10)                                    │
│                                                                  │
│    terminal = await _execute_step()  ─────────┐                 │
│    if terminal: return                          │                 │
│                                                  │                 │
└──────────────────────────────────────────────────┼────────────────┘
                                                  │
                                                  ▼
┌────────────────────────────────────────────────────────────────┐
│ _execute_step()                                                  │
│                                                                  │
│  1. model.stream_chat(history) ─► chunks                        │
│  2. parser.feed(chunk) ─► events:                               │
│        ThinkingChunk → renderer.render_thinking_chunk()         │
│        ThinkingEnd   → renderer.render_thinking_end()           │
│        ToolCallComplete → break                                 │
│  3. parser.finalize() (cleanup)                                 │
│  4. history.append(assistant_msg)                               │
│                                                                  │
│  5. permissions.check(tool, args)                               │
│        AUTO_APPROVED → seguir                                   │
│        NEEDS_APPROVAL → ask_approval():                         │
│            APPROVE_ONCE/SESSION/ALWAYS → seguir                 │
│            DENY → append observation, return False              │
│            DENY_WITH_FEEDBACK → idem + feedback al modelo       │
│                                                                  │
│  6. renderer.render_tool_call(name, args)                       │
│  7. tool_def.validate_and_call(args) → result | exception       │
│  8. budget.record(StepRecord)                                   │
│                                                                  │
│  9. if tool.category == FLOW:                                   │
│        responder_al_usuario → render_final_answer + return True │
│        preguntar_al_usuario → render_question + return True     │
│     else:                                                        │
│        render_observation                                        │
│        history.append(observation_msg)                          │
│        return False                                              │
└────────────────────────────────────────────────────────────────┘
```

### Ejemplo concreto

```
›› leeme el archivo notes.md de context/
─────────────────────────────────────────────────────────
El usuario quiere ver un archivo. Voy a leerlo.
▶ read_file(path='/Users/coco/.../workspace/context/notes.md')
← Línea 1
  Línea 2
  Línea 3
Listo, ya tengo el contenido. Lo paso al usuario.
▶ responder_al_usuario(mensaje='Acá está el contenido: ...')
╭─ Respuesta ─────────────────────────────────────────────╮
│ Acá está el contenido:                                  │
│ Línea 1                                                  │
│ Línea 2                                                  │
│ Línea 3                                                  │
╰──────────────────────────────────────────────────────────╯
```

---

## 6. Sistema de permisos

### Tabla de despacho

| Tool category | Whitelist match | Always rule match | Session match | Resultado          |
|---------------|-----------------|-------------------|---------------|--------------------|
| FLOW          | n/a             | n/a               | n/a           | AUTO_APPROVED      |
| READ          | sí              | n/a               | n/a           | AUTO_APPROVED      |
| READ          | no              | sí                | n/a           | AUTO_APPROVED      |
| READ          | no              | no                | sí            | AUTO_APPROVED      |
| READ          | no              | no                | no            | NEEDS_APPROVAL     |
| WRITE/DELETE  | n/a             | sí                | n/a           | AUTO_APPROVED      |
| WRITE/DELETE  | n/a             | no                | sí            | AUTO_APPROVED      |
| WRITE/DELETE  | n/a             | no                | no            | NEEDS_APPROVAL     |

### Códigos del prompt de aprobación

| Código   | Efecto                                                              |
|----------|---------------------------------------------------------------------|
| `y`      | Aprobar esta llamada solamente.                                     |
| `s`      | Aprobar para esta sesión (mismo `tool` + `args` → auto-aprobado).   |
| `a`      | Aprobar siempre. Persiste en `config/permissions.toml`.             |
| `n`     | Rechazar. La tool no se ejecuta; el modelo recibe DENIED.            |
| Enter    | Equivalente a `n`.                                                   |
| [texto]  | Rechazar y mandar `texto` como feedback al modelo en la observation.|

### Pattern matching de always-allows

Cuando aprobás `a` para `write_file("/output/q3.pptx", ...)`, se genera:

```toml
[[allow]]
tool = "write_file"
path_pattern = "/Users/coco/.../workspace/output/*"
```

El patrón usa `fnmatch`, así que `*` no es recursivo (no matchea
subdirectorios). Para scope más amplio, el usuario puede editar el TOML
manualmente.

---

## 7. Parser XML streaming

### Máquina de estados

```
                  ┌──────────────────┐
                  │     OUTSIDE      │◄─────────────┐
                  └────┬─────────┬───┘              │
                       │         │                   │
        <thinking>   │         │   <tool name="x">│
                       │         │                   │
                       ▼         ▼                   │
              ┌──────────────┐ ┌──────────────┐    │
              │ IN_THINKING  │ │   IN_TOOL    │    │
              └──────┬───────┘ └──────┬───────┘    │
                     │                │             │
                     │  </thinking>   │  </tool>    │
                     └────────────────┴─────────────┘
```

### Hold-back logic

Para tolerar fragmentación de chunks en lugares delicados:

- **OUTSIDE**: si el buffer termina con un `<` sin `>` posterior,
  retiene desde el `<` (puede ser un opening tag parcial).
- **IN_THINKING**: busca el sufijo más largo del buffer que sea prefijo
  de `</thinking>` y retiene desde ahí.
- **IN_TOOL**: ídem con `</tool>`.

Eventualmente el siguiente chunk llega y completa el tag, o `finalize()`
flushea con un `ParseError` si quedó algo abierto.

### Tolerancia probada por tests

- Whitespace dentro de tags: `< thinking >`, `< /tool >`.
- Case insensitive: `<THINKING>`, `<Tool>`.
- Quotes dobles o simples en `name="..."` / `name='...'`.
- Self-closing tags: `<tool name="ping" />`.
- Char-by-char streaming: cada caracter por separado.
- Múltiples bloques `<thinking>` antes de un `<tool>`.
- Determinismo: mismo input genera mismos eventos sin importar la
  fragmentación (test parametrizado).

---

## 8. Tests

**351 tests passing**, distribuidos:

| Archivo                                | Tests | Cobertura                                                |
|----------------------------------------|-------|----------------------------------------------------------|
| `test_tools_base.py`                   | 15    | Decorador `@tool`, validación, `to_prompt_section`       |
| `test_tools_registry.py`               | 11    | Registro, escaneo de módulos, `load_builtin_tools`       |
| `test_tools_filesystem.py`             | 21    | read/write/delete/list con tmp_path, edge cases          |
| `test_tools_flow.py`                   | 11    | responder/preguntar, parsing de opciones, JSON           |
| `test_tools_pptx.py`                   | 46    | Schemas, generate/read/edit/from_template, errores       |
| `test_tools_xlsx.py`                   | 57    | Schemas, generate/read/edit/append, errores              |
| `test_session_log.py`                  | 25    | Logger JSONL, encoding, lazy file open, manejo I/O       |
| `test_agent_parser.py`                 | 42    | Parser streaming, partials, recovery, determinismo       |
| `test_permissions_manager.py`          | 17    | Reglas, whitelist, sticky, persistencia TOML             |
| `test_permissions_prompts.py`          | 28    | parse codes, panel rendering, ask_approval               |
| `test_agent_prompts.py`                | 13    | System prompt builder, load_context_files                |
| `test_agent_budget.py`                 | 26    | Tracker, parse continuation, panel, ask                  |
| `test_ui_console.py`                   | 17    | Cada render method, truncation, panels                   |
| `test_agent_loop.py`                   | 17    | Integración: turnos completos + emisión de eventos al log|
| **Total**                              | **351** |                                                        |

Los tests de `test_tools_pptx.py` y `test_tools_xlsx.py` se skipean
automáticamente si `python-pptx` / `openpyxl` no están instalados
(`pytest.importorskip`), así que la suite sigue corriendo en setups
mínimos sin la extra `[office]`.

### Estrategia

- **Unit tests con tmp_path**: para tools de filesystem y permisos
  persistentes (cada test tiene su propio tmp).
- **StringIO consoles**: para tests de UI que verifican output sin
  ensuciar stdout.
- **Input func inyectable**: las funciones que leen del usuario
  (`ask_approval`, `ask_continuation`) aceptan un callable como
  parámetro; los tests pasan lambdas.
- **ScriptedModel**: para integración del loop, un `ModelClient` fake
  que devuelve respuestas pre-programadas. Permite simular turnos
  completos sin Ollama.
- **Parametrización**: tests determinísticos del parser ejecutan el
  mismo input con distintos puntos de fragmentación.

---

## 9. Cómo extender

### Agregar una tool nueva

```python
# wso/tools/my_module.py

from wso.tools.base import PermissionCategory, tool


@tool(
    name="generate_xlsx",
    category=PermissionCategory.WRITE,
    description="Genera un .xlsx desde una lista de filas.",
    args_schema={
        "output_path": "ruta absoluta donde escribir el .xlsx",
        "rows_json": "JSON array con las filas. Ej: [[\"A\",\"B\"],[1,2]]",
    },
)
def generate_xlsx(output_path: str, rows_json: str) -> str:
    # ... tu lógica acá ...
    return f"Generado: {output_path}"
```

Después editás `tools/registry.py:load_builtin_tools()` para importar
`my_module`. La tool aparece automáticamente en el system prompt y
queda gateada por permissions con la categoría declarada.

**Para tools con estructuras complejas**: las tools de pptx ilustran el
patrón JSON-in-string (decisión 3.15). Si tu tool necesita pasar una
lista o un objeto anidado, exponé un único arg `*_json` (string) y
parseá + validá con Pydantic dentro del handler. Si una dep externa
opcional es necesaria (como `python-pptx` o `openpyxl`), importala lazy
dentro de un `_require_*()` helper para que el módulo siga importándose
sin la extra instalada.

### Agregar un provider cloud

Los cuatro providers principales (Ollama, Anthropic, OpenAI, Google)
ya están implementados. Para agregar uno nuevo:

1. Implementar la clase concreta en `wso/agent/model/<provider>.py`
   heredando de `ModelClient`. Override `stream_chat` y `model_name`.
2. Editar `wso/agent/model/factory.py` para despachar el caso.
3. Agregar la API key correspondiente al `.env.example` y a `config.py`.
4. Si el provider es OpenAI-compatible (Together.ai, Groq, OpenRouter,
   etc), no hace falta una clase nueva: alcanza con setear
   `WSO_OPENAI_BASE_URL` apuntando al endpoint y usar `WSO_CLOUD_PROVIDER=openai`.

### Cambiar el estilo de la UI

Todos los métodos de rendering están en `wso/ui/console.py`. Las
constantes `_STYLE_*` al tope del archivo controlan colores y prefijos.
Cambiar el aspecto no requiere tocar lógica del loop.

### Custom system prompt

Modificar `_SYSTEM_PROMPT_TEMPLATE` en `wso/agent/prompts.py`. Los
placeholders `{tools_section}` y `{context_section}` se reemplazan
automáticamente.

---

## 10. Roadmap

### v0.1 (cerrado — este release)

- [x] Patrón híbrido tools + escape hatch declarado
- [x] Loop conversacional con elicitación explícita
- [x] Parser XML streaming tolerante con tests determinísticos
- [x] 6 tools de v1: read/write/delete/list + responder/preguntar
- [x] Sistema de permisos completo (categorías + sticky + whitelist)
- [x] Persistencia TOML
- [x] Cliente Ollama con `num_ctx` configurable (control del KV cache)
- [x] Cliente Anthropic (Claude)
- [x] Cliente OpenAI (GPT + endpoints OpenAI-compatible vía `base_url`,
      ej: OpenRouter, Together.ai, Groq, Azure)
- [x] Cliente Google (Gemini)
- [x] System prompt con sección anti-refusal para reforzar tool use
- [x] UI Rich con streaming visible
- [x] Budget de 10 con prompt de continuación
- [x] 214 tests pasando

### v0.2 (en progreso)

- [x] Tools de PowerPoint (`python-pptx`): `generate_pptx`, `read_pptx`,
      `edit_pptx_slide`, `generate_pptx_from_template`. JSON-in-string
      como workaround para args complejos (decisión 3.15).
- [x] Tools de Excel (`openpyxl`): `generate_xlsx`, `read_xlsx`,
      `edit_xlsx_cell`, `append_xlsx_rows`. Soporta múltiples sheets,
      headers con bold, fórmulas como strings (`"=A1+B1"`).
- [x] Logging estructurado a `logs/session_*.jsonl`. SessionLogger
      opt-in vía `WSO_LOG_ENABLED=true`. Eventos: session_start/end,
      turn_start/end, model_response, tool_call, permission,
      observation, error, budget_continuation. Útil para debug
      post-mortem, auditoría, y replay de sesiones reales.
- [ ] Tools de LinkedIn (RSS-based, sin scraping)
- [ ] CDATA o entity escaping para args con XML (desbloquea estructuras
      anidadas sin pasar por JSON)

### v0.3 (futuro)

- [ ] `run_python` con sandbox real (subprocess aislado + cwd limitado a
      `workspace/` + timeout + EXECUTE permission gateado). Es el escape
      hatch del patrón híbrido (decisión 3.1) para tareas raras que no
      merecen una tool tipada propia. Diferido a v0.3 porque hacerlo bien
      requiere decisiones de diseño propias del sandbox (subprocess vs
      RestrictedPython vs WASM) que no queremos rushear.
- [ ] Persistencia de historial entre sesiones
- [ ] Memoria de largo plazo (RAG sobre `/context`)
- [ ] Modo no-conversacional para batch jobs
- [ ] Tools async (HTTP, base de datos)
- [ ] Editar args antes de aprobar (en lugar de solo y/n)

---

## Notas finales

El diseño priorizó **decisiones explícitas y reversibles** sobre
optimización temprana. Cada elección arquitectónica (XML vs JSON, tools
vs code generation, sticky permissions, etc.) fue discutida y
documentada antes de codear.

La cobertura de tests es alta (214 tests) porque varios módulos —
parser, permissions, decorador `@tool` — son lógica delicada donde
los bugs no aparecen hasta que el modelo emite algo raro. Probarlos
exhaustivamente cuesta poco y ahorra debugging costoso después.

Si encontrás un caso que el harness no maneja bien, los puntos de
extensión están claros: nueva tool, nuevo provider, nuevo render
style. Los principios fundacionales (patrón C, modelo 3, XML, sticky
permissions) son intencionalmente estables.
