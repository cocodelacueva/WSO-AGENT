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

### 3.17 Normalización tolerante del JSON de slides (post-prueba 2)

La prueba 2 mostró que los modelos chicos (qwen2.5-coder:14b) fallan
repetidamente contra el discriminated union de slides: omiten `layout`,
usan `content`/`text` en vez de `bullets`, escriben `bullets` como string,
o usan sinónimos del layout. Cada fallo gasta un step del budget y confunde
al modelo (vimos 5 reintentos seguidos por el mismo slide).

Decisión: **normalizar cada slide-dict a la forma canónica antes de validar**
(`_normalize_slide_dict` en `pptx.py`), sin relajar el schema interno. Reglas:

- `layout` ausente o desconocido → se infiere (image_path→image, left/right_
  bullets→two_content, subtitle sin body→title, default→content).
- sinónimos de layout (`portada`, `bullet`, `seccion`, …) → nombre canónico.
- `content`/`text`/`body`/`points`/`items` → se mapean a `bullets` (o a
  `subtitle` en layouts title/section).
- `bullets` como string → se splitea por líneas; como lista → se stringifica.
- un slide-dict suelto (no envuelto en lista) → se envuelve.

Si tras normalizar sigue sin validar (ej: falta `title`, que no se puede
inventar), se levanta un `ValueError` con un mensaje que muestra la forma
esperada por layout — mucho más útil que el `union_tag_not_found` crudo de
Pydantic. El schema canónico (`pptx_schemas.py`) queda intacto; toda la
tolerancia vive en la capa de parsing.

### 3.18 Aliases de args en el decorador `@tool`

En la prueba 2 el modelo emitió `slides` en vez de `slides_json` y la tool
falló con "Field required". Generalizamos una solución: `@tool` acepta un
`aliases: dict[str, str]` (`alias → arg canónico`). `validate_and_call`
remapea las claves antes de validar, sin pisar lo explícito (si vienen
ambos, gana el canónico). Las tools de PPTX registran `slides`/`slides_xml`
→ `slides_json` y `updates` → `updates_json`. El decorador valida en
registro que cada alias apunte a un arg real y no colisione con uno.

### 3.19 Cap de tamaño en las tools de lectura

`read_pdf`/`read_docx`/`read_pptx` pueden devolver documentos enormes (en la
prueba 2, un PDF de 26 páginas). Con `num_ctx` chico, una sola lectura
desaloja del contexto el system prompt y la tarea, y el modelo termina
generando un deck genérico que ignora lo leído. Mitigación en dos frentes:

- `num_ctx` default subido a 16384 (decisión de config, ver README).
- `max_chars` (default 16000) en las tres tools de lectura, vía el helper
  `truncate_with_notice` (`base.py`): trunca a un tamaño predecible y agrega
  un aviso visible de cuánto quedó afuera y cómo leer el resto. `read_pdf`
  trunca en límite de página y expone `start_page` para leer por tramos.

El aviso es clave: sin él, el modelo trata el texto truncado como completo.

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
│   ├── pdf.py                    # read_pdf (texto por página, start_page/max_chars)
│   ├── docx.py                   # read_docx (párrafos, headings, tablas, max_chars)
│   ├── browser.py                # browser_* (CDP attach a Chrome real, v0.3)
│   └── code.py                   # run_python (sandbox subprocess, v0.3)
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
`filesystem`, `flow`, `pptx`, `xlsx`, `pdf` y `docx`, y devuelve un
registry poblado con 16 tools.

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
- Último arg abierto sin cerrar: el modelo emite `<slides_json>[...]` y
  salta directo a `</tool>` olvidando `</slides_json>`. El parser captura
  igual ese arg hasta el final del body (recovery observado con modelos
  locales chicos en decks largos; evita loops de "Field required").
- Determinismo: mismo input genera mismos eventos sin importar la
  fragmentación (test parametrizado).

---

## 8. Tests

**401 tests passing**, distribuidos:

| Archivo                                | Tests | Cobertura                                                |
|----------------------------------------|-------|----------------------------------------------------------|
| `test_tools_base.py`                   | 23    | Decorador `@tool`, validación, aliases, `truncate_with_notice` |
| `test_tools_registry.py`               | 11    | Registro, escaneo de módulos, `load_builtin_tools`       |
| `test_tools_filesystem.py`             | 20    | read/write/delete/list con tmp_path, edge cases          |
| `test_tools_flow.py`                   | 11    | responder/preguntar, parsing de opciones, JSON           |
| `test_tools_pptx.py`                   | 55    | Schemas, normalización tolerante, generate/read/edit/template |
| `test_tools_xlsx.py`                   | 57    | Schemas, generate/read/edit/append, errores              |
| `test_tools_pdf.py`                    | 14    | read_pdf, validación de path, start_page, truncado       |
| `test_tools_docx.py`                   | 18    | read_docx, párrafos/headings/tablas, max_chars           |
| `test_session_log.py`                  | 25    | Logger JSONL, encoding, lazy file open, manejo I/O       |
| `test_agent_parser.py`                 | 45    | Parser streaming, partials, recovery, determinismo       |
| `test_permissions_manager.py`          | 21    | Reglas, whitelist, sticky, persistencia TOML             |
| `test_permissions_prompts.py`          | 24    | parse codes, panel rendering, ask_approval               |
| `test_agent_prompts.py`                | 15    | System prompt builder, guardrails, load_context_files    |
| `test_agent_budget.py`                 | 28    | Tracker, parse continuation, panel, ask                  |
| `test_ui_console.py`                   | 17    | Cada render method, truncation, panels                   |
| `test_agent_loop.py`                   | 17    | Integración: turnos completos + emisión de eventos al log|
| **Total**                              | **401** |                                                        |

Los tests de `test_tools_pptx.py`, `test_tools_xlsx.py`, `test_tools_pdf.py`
y `test_tools_docx.py` se skipean automáticamente si su dep
(`python-pptx`/`openpyxl`/`pypdf`/`python-docx`) no está instalada
(`pytest.importorskip`), así que la suite sigue corriendo en setups
mínimos sin la extra `[office]`. Los tests de texto de `read_pdf` usan
`reportlab` (extra `[dev]`) para generar PDFs con texto extraíble.

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

### v0.2 (cerrado)

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
- [ ] CDATA o entity escaping para args con XML (desbloquea estructuras
      anidadas sin pasar por JSON). Diferido — no apareció un caso de
      uso concreto todavía.

**Cancelado de v0.2:** Tools de LinkedIn RSS-based. Se descartó porque
LinkedIn bloquea agresivamente RSS y Sales Navigator (caso de uso real
del estudio) no expone feed. La alternativa correcta es el browser bridge
(v0.3) — usar el Chrome real del usuario, ya logueado, sin pasar
credenciales.

### v0.3 (en progreso)

- [x] **Browser bridge mínimo** — el agente maneja el Chrome real del
      usuario (vía Playwright + CDP attach) reutilizando sus sesiones
      logueadas. Reemplaza el approach LinkedIn-RSS (no viable). Sirve
      como puente general para cualquier app web sin API: LinkedIn,
      Sales Navigator, Notion, Airtable, dashboards internos, etc.
      9 tools implementadas: `browser_open_tab`, `browser_navigate`,
      `browser_read_page`, `browser_click`, `browser_type`,
      `browser_screenshot`, `browser_wait_for`, `browser_close_tab`,
      `browser_scroll` (`wso/tools/browser.py`). Categoría de permiso `BROWSER` con sticky
      por dominio a nivel de sesión (`permissions/manager.py`). Las
      operaciones corren en un worker thread dedicado para no chocar con
      el event loop (ver A.6). Spike previo validó el attach contra Sales
      Navigator real. Falta: E2E manual y endurecimiento. Ver Apéndice A.
- [x] `run_python` con sandbox real (`wso/tools/code.py`). Escape hatch del
      patrón híbrido (decisión 3.1) para tareas raras que no merecen una tool
      tipada propia. Decisiones cerradas: **subprocess aislado**
      (`sys.executable -I`) con cwd en `workspace/run`, timeout de pared,
      resource limits (RLIMIT_CPU/FSIZE en Unix, RLIMIT_AS solo Linux), red
      deshabilitada best-effort (subclase de socket que bloquea en `connect`,
      sin romper imports de `ssl`/`http`/`urllib`), y gate de permiso EXECUTE
      (sin auto-aprobación) como control real. Se descartaron RestrictedPython
      (frágil) y WASM (sobredimensionado, sin acceso al FS del usuario).
      Política de imports: stdlib completa, sin red.
- [x] Persistencia de historial entre sesiones (`wso/history_store.py`).
      Opt-in vía `WSO_HISTORY_PERSIST`. Guarda el historial (user/assistant,
      sin system prompt) tras cada turno y lo restaura al arrancar. Escritura
      atómica (tmp+replace), cap de mensajes, no-op por default. Comando
      `/reset` en el REPL para empezar de cero.

### v0.4 (futuro)

- [ ] **E2E manual del browser + endurecimiento** — correr `wso` real contra
      LinkedIn/Sales Navigator (leer/resumir posts), más stealth, delays
      randomizados y detección de session expiry / pantallas de login a nivel
      del loop. Cierra lo pendiente del browser bridge (Apéndice A.10).
- [ ] Memoria de largo plazo (RAG sobre `/context`) — embeddings + store +
      retrieval, para no cargar todo el contexto siempre.
- [ ] Modo no-conversacional para batch jobs (`wso --task "..."`), habilita
      scheduling y automatizaciones.
- [ ] Tools async (HTTP, base de datos) — y resolver el contrato async que ya
      asomó con el worker thread del browser (A.6).
- [ ] Editar args antes de aprobar (en lugar de solo y/n) en el prompt de
      permiso.

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

---

## Apéndice A — Plan del Browser bridge (v0.3)

Este apéndice condensa las decisiones que ya discutimos sobre cómo el
agente va a manejar un navegador real, para no tener que re-discutirlas
cuando volvamos al tema. **No es código todavía** — es el scope acordado.

### A.1 Por qué browser bridge en lugar de scraping / RSS

El approach original "tools de LinkedIn vía RSS" se descartó por dos
razones concretas del estudio:

1. **LinkedIn bloquea RSS agresivamente.** Los bridges públicos
   (RSSHub, etc.) tienen cobertura inconsistente y dependen de hacks
   que se rompen con cada cambio de UI de LinkedIn.
2. **Sales Navigator no expone RSS.** Es la herramienta real que usa
   White Suit. Cualquier solución que no la cubra es teatro.

Una alternativa intermedia — scraping con `requests`/`httpx` — falla
por anti-bot detection (LinkedIn detecta requests sin headers de
navegador real, fingerprint de TLS, etc.) y por requerir credenciales
en el `.env`.

El **browser bridge** evita ambos problemas: el agente no tiene
credenciales (las cookies viven en tu Chrome), y LinkedIn ve un
navegador real porque ES tu navegador real.

### A.2 Stack técnico

- **Playwright** como engine de automatización
  (`extra: browser = ["playwright>=1.40"]`).
- **CDP attach** vía `playwright.chromium.connect_over_cdp(url)` — el
  agente NO arranca un Chrome propio; se conecta a uno que vos ya
  iniciaste. Esto preserva tu user-data-dir, tus cookies, tus
  extensiones, todo.
- **API sync** (`playwright.sync_api`) para mantener la convención de
  WSO de que las tools son funciones sync. El loop sigue siendo async,
  pero las tools individuales bloquean — el patrón actual.
- **Config nueva**: `WSO_BROWSER_CDP_URL` (default
  `http://localhost:9222`).

### A.3 UX para arrancar Chrome

El usuario tiene que iniciar Chrome con el flag de debugging. Dos
caminos:

1. **Script helper en `scripts/`**: `launch-chrome-cdp.sh` (Mac/Linux)
   y `.ps1` (Windows) que iniciás vos antes de `wso`. Documentado en
   README.
2. **Alias en `.zshrc`/`.bashrc`**: `alias wso-chrome="open -a Google\ Chrome --args --remote-debugging-port=9222"`.

Cuestión abierta: ¿usar el `user-data-dir` default (todas tus
cuentas) o uno separado (perfil aislado solo para WSO)? Trade-off:

- **Default**: pleno acceso a todas tus sesiones. Más útil pero más
  riesgoso si el agente hace algo raro.
- **Separado**: aislamiento, pero tenés que loguearte por separado en
  cada sitio. Menos cómodo, más seguro.

Recomendación inicial: documentar ambos, default = separado, opt-in al
default con flag.

### A.4 Tools propuestas (alcance mínimo)

| Tool                   | Categoría | Args                          | Comentario                              |
|------------------------|-----------|-------------------------------|-----------------------------------------|
| `browser_open_tab`     | BROWSER   | url                           | Crea tab nueva, foco automático.        |
| `browser_navigate`     | BROWSER   | url                           | Navega tab activa.                      |
| `browser_close_tab`    | BROWSER   | -                             | Cierra tab activa.                      |
| `browser_read_page`    | READ      | (max_chars opcional)          | Texto del DOM (sin scripts ni navs).    |
| `browser_screenshot`   | READ      | output_path                   | Guarda PNG. Útil para debug.            |
| `browser_click`        | BROWSER   | selector (CSS o texto)        | Click humano (con scroll-into-view).    |
| `browser_type`         | BROWSER   | selector, text                | Type human-like en input.               |
| `browser_wait_for`     | READ      | selector, timeout_ms          | Espera elemento (SPA-friendly).         |
| `browser_scroll`       | READ      | direction, amount             | Scroll para revelar contenido lazy.     |

Notas:

- `selector` puede ser CSS (`button.submit`) o texto (`text=Enviar`).
  Playwright soporta ambos.
- `browser_read_page` es READ porque solo lee. Eso significa que entra
  en el whitelist de auto-aprobación junto con `read_file` — válido
  porque ya estás viendo esa página vos.
- `browser_screenshot` también READ, pero su salida va a un path que
  podría estar fuera de whitelist — entonces el path se valida como
  cualquier write.

### A.5 Categoría de permiso `BROWSER`

Nueva entrada en `PermissionCategory`. Política sugerida:

- **No auto-aprobar nada por default.**
- **Sticky por dominio**: si aprobás `browser_navigate(url)` para
  `linkedin.com`, las navegaciones futuras a `linkedin.com/*` también
  se aprueban. Aprobar es a nivel de **dominio**, no de URL completa.
- **Acciones (click, type) tienen sticky más estricto**: aprobás por
  dominio + por tipo de acción. Ej: `[allow] domain=linkedin.com action=click`
  cubre todos los clicks en LinkedIn pero no los `browser_type`.
- **Sin política `always` persistente al inicio**: solo sesión. Hasta
  que el modelo esté maduro suficiente, evitamos auto-aprobar entre
  sesiones para algo tan poderoso como manejo de navegador.

### A.6 Async / sync

Playwright tiene dos APIs: `sync_api` y `async_api`. Las tools de WSO
son funciones sync (corren dentro del loop async pero blocking). Usar
`sync_api` directamente requiere que **no esté corriendo un event loop**
en el mismo thread cuando se llama.

Esto choca con `asyncio.to_thread` que el loop usa. Camino limpio:

- Cada tool de browser corre vía `asyncio.to_thread`, que abre un thread
  worker donde Playwright sync_api puede operar tranquilo.
- El cliente Playwright (`browser`, `context`, `page`) es **estado
  global del módulo `wso/tools/browser.py`** con lazy-init en el primer
  call. Cleanup en `atexit` o en el `session_end` del loop.

Alternativa: usar `async_api` y hacer las tools async. Es más limpio
arquitecturalmente pero requiere cambios en el contrato de
`ToolDefinition.validate_and_call` para soportar awaitables. Trade-off
para evaluar cuando lleguemos.

### A.7 Riesgos conocidos

- **Anti-bot de LinkedIn / Sales Navigator**: incluso con browser real
  pueden detectarte (mouse movement patterns, timing, CDP detection).
  Mitigaciones: `playwright-stealth`, delays randomizados, hacer reads
  pasivos antes que actions, no spammear. **Aceptamos que pueden
  bloquearte si abusás** — es responsabilidad del usuario.
- **CDP detection**: algunos sitios detectan que Chrome corre con CDP
  abierto vía `navigator.webdriver` u otros checks. Workaround:
  arrancar Chrome con `--disable-blink-features=AutomationControlled`.
- **Iframes**: LinkedIn usa varios. `browser_click` necesita poder
  apuntar dentro de un iframe (Playwright lo maneja con `frame_locator`).
- **Cookies de terceros / consent dialogs**: pueden aparecer
  inesperadamente. Las tools tienen que ser tolerantes a "selector no
  encontrado" en lugar de tirar excepción dura.
- **Session expiry**: si tu sesión de LinkedIn caduca a mitad de un
  turno, el agente puede confundirse. El loop debería poder detectar
  pantallas de login y abortar con un mensaje claro al usuario.

### A.8 Tests

- **Tests unit del cliente**: mock de Playwright (`pytest-playwright`
  trae fixtures útiles) — verificar que las tools mapean args a
  llamadas correctas.
- **Tests de integración local**: levantar un servidor `http.server`
  con HTML fixturas, conectar Playwright real, verificar el end-to-end
  contra un sitio controlado.
- **Tests E2E con LinkedIn**: NO en CI. Documentados como manuales
  ("script manual: probar leer feed personal una vez").

### A.9 Cuándo empezar

Esta sección queda como contrato para cuando volvamos al tema. Antes
de codear:

1. Decidir definitivamente el caso de uso #1 (probablemente: "leeme
   los últimos N posts de Sales Navigator y resumímelos").
2. Validar manualmente que Playwright + CDP attach funciona en tu
   Chrome real con tu Sales Navigator logueado. Si LinkedIn detecta el
   automation y te limita, el plan completo cambia.
3. Recién entonces, abrir el iter de browser bridge.

### A.10 Resultado del spike y decisiones tomadas (2026-06)

El spike (`scripts/spike_cdp_attach.py` + `scripts/launch-chrome-cdp.sh`)
se corrió contra Chrome real y **validó la base**:

- `connect_over_cdp` se attachea al Chrome del usuario y ve sus tabs
  reales (no un navegador limpio).
- Tras loguearse, `browser_read_page` (vía `innerText`) devuelve el
  contenido real de Sales Navigator (`Home / Accounts / Leads / ...`),
  no una pantalla de login.
- `navigator.webdriver` da **`false`**: LinkedIn no detectó automation
  con el flag `--disable-blink-features=AutomationControlled`.

Decisiones cerradas a partir del spike:

- **Perfil**: aislado (`~/.wso-chrome`) como default; el usuario se
  loguea una vez y persiste. `--default` queda como opt-in documentado.
- **Sync vs async (A.6)**: resuelto con **worker thread dedicado**. El
  loop ejecuta las tools síncronamente dentro del thread del event loop,
  donde Playwright `sync_api` se niega a operar. Un thread propio que es
  dueño de la conexión y los objetos (page/context, no thread-safe)
  resuelve ambos problemas sin volver async el contrato de tools.
- **Espera de SPA**: las lecturas/acciones esperan render real; existe
  `browser_wait_for` para el modelo y `browser_read_page` avisa cuando el
  body está vacío (probable login wall / SPA sin renderizar).
- **Permisos**: `BROWSER` nunca auto-aprueba por default. Sticky de
  sesión **por dominio** para navegación (open_tab/navigate comparten
  grupo); las acciones sin URL (click/type) caen al sticky por args
  exactos. Nada persiste entre sesiones.

Pendiente para el siguiente iter: E2E manual leyendo posts reales,
endurecimiento (stealth/delays, detección de session expiry y pantallas
de login a nivel del loop).
