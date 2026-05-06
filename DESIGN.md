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
              ┌────────────────────┼─────────────────────┐
              │                    │                     │
       ┌──────▼──────┐    ┌────────▼──────┐    ┌────────▼──────┐
       │ OllamaClient│    │AnthropicClient│    │  OpenAIClient │
       │   (local)   │    │  (stub v2)    │    │  (stub v2)    │
       └─────────────┘    └───────────────┘    └───────────────┘
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
de flujo) con un escape hatch `run_python` previsto para v2. La razón:
los casos repetitivos (PPT, Excel, LinkedIn) merecen tools tipadas para
ser predecibles y auditables; los casos raros pueden caer al sandbox de
Python para no codear una tool por cada combinación.

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

### 3.13 Abstracción de modelo desde el día 1

`ModelClient` ABC con factory que despacha por `WSO_MODE`. v1 implementa
solo `OllamaClient`; los stubs de Anthropic y OpenAI están listos para
v2. La estructura ya soporta ambos modos cuando llegue el momento.

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
│       ├── ollama.py             # OllamaClient (v1 implementado)
│       ├── anthropic.py          # AnthropicClient (stub v2)
│       └── openai.py             # OpenAIClient (stub v2)
├── tools/
│   ├── base.py                   # @tool decorador, ToolDefinition, ToolValidationError
│   ├── registry.py               # ToolRegistry, load_builtin_tools
│   ├── filesystem.py             # read_file, write_file, delete_file, list_directory
│   ├── flow.py                   # responder_al_usuario, preguntar_al_usuario
│   └── code.py                   # run_python (stub v2)
├── permissions/
│   ├── manager.py                # PermissionManager + AlwaysAllowRule
│   └── prompts.py                # ask_approval, parse_approval_input
└── ui/
    └── console.py                # ConsoleRenderer (Rich wrapper)
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
`stream_chat(messages) -> AsyncIterator[str]`. Implementaciones
concretas por provider.

**`tools/base.py`** — El corazón del sistema de tools:
- `PermissionCategory` enum
- `ToolDefinition` con `validate_and_call()` y `to_prompt_section()`
- `@tool` decorador que infiere args desde la signature, valida
  schema contra firma, construye un Pydantic model dinámico para
  validación runtime

**`tools/registry.py`** — `ToolRegistry` que escanea módulos buscando
funciones con `_tool_def` adjunto. `load_builtin_tools()` importa
`filesystem` y `flow` y devuelve un registry poblado.

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

**214 tests passing**, distribuidos:

| Archivo                                | Tests | Cobertura                                          |
|----------------------------------------|-------|----------------------------------------------------|
| `test_tools_base.py`                   | 15    | Decorador `@tool`, validación, `to_prompt_section` |
| `test_tools_registry.py`               | 11    | Registro, escaneo de módulos, `load_builtin_tools` |
| `test_tools_filesystem.py`             | 21    | read/write/delete/list con tmp_path, edge cases    |
| `test_tools_flow.py`                   | 11    | responder/preguntar, parsing de opciones, JSON     |
| `test_agent_parser.py`                 | 42    | Parser streaming, partials, recovery, determinismo |
| `test_permissions_manager.py`          | 17    | Reglas, whitelist, sticky, persistencia TOML       |
| `test_permissions_prompts.py`          | 28    | parse codes, panel rendering, ask_approval         |
| `test_agent_prompts.py`                | 13    | System prompt builder, load_context_files          |
| `test_agent_budget.py`                 | 26    | Tracker, parse continuation, panel, ask            |
| `test_ui_console.py`                   | 17    | Cada render method, truncation, panels             |
| `test_agent_loop.py`                   | 11    | Integración: turnos completos, errores, budget     |
| **Total**                              | **214** |                                                  |

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
    name="generate_pptx",
    category=PermissionCategory.WRITE,
    description="Genera un .pptx desde markdown.",
    args_schema={
        "input_md": "ruta absoluta del .md de entrada",
        "output_pptx": "ruta absoluta donde escribir el .pptx",
    },
)
def generate_pptx(input_md: str, output_pptx: str) -> str:
    # ... tu lógica acá ...
    return f"Generado: {output_pptx}"
```

Después editás `tools/registry.py:load_builtin_tools()` para importar
`my_module`. La tool aparece automáticamente en el system prompt y
queda gateada por permissions con la categoría declarada.

### Agregar un provider cloud

1. Implementar la clase concreta en `wso/agent/model/<provider>.py`
   heredando de `ModelClient`. Override `stream_chat` y `model_name`.
2. Editar `wso/agent/model/factory.py` para despachar el caso.
3. Agregar la API key correspondiente al `.env.example` y a `config.py`.

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
- [x] Cliente Ollama
- [x] UI Rich con streaming visible
- [x] Budget de 10 con prompt de continuación
- [x] 214 tests pasando

### v0.2 (próximo)

- [ ] `run_python` con sandbox real (subprocess + AST allowlist)
- [ ] Tools de Office (PPT, Excel) usando `python-pptx` y `openpyxl`
- [ ] Tools de LinkedIn (RSS-based, sin scraping)
- [ ] Cliente Anthropic (Claude)
- [ ] Cliente OpenAI (compatibilidad GPT/Azure)
- [ ] Logging estructurado a `logs/session_*.jsonl`
- [ ] CDATA o entity escaping para args con XML

### v0.3 (futuro)

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
