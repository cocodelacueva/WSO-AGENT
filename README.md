# White Suit Operator — WSO

Agente de terminal para asistir tareas del estudio. Diseñado para correr
local (Ollama) o contra modelos cloud (Anthropic, OpenAI, Google) usando
la misma interfaz.

**Versión:** 0.1.0 (Local Agent Prototype)

---

## Arquitectura

WSO es un harness agéntico conversacional con cuatro principios de diseño:

1. **Patrón híbrido** — tools tipadas para operaciones comunes,
   `run_python` como escape hatch para casos no previstos (v2).
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
├── automations/       # scripts custom invocables vía run_python (v2)
└── output/            # entregables generados

config/                # settings.toml y permissions.toml persistentes
logs/                  # audit trail de sesiones
```

## Setup

```bash
# 1. Instalar dependencias del sistema
ollama pull qwen2.5-coder:32b

# 2. Instalar dependencias Python
pip install -r requirements.txt
# o en modo editable:
pip install -e ".[dev]"

# 3. Configurar entorno
cp .env.example .env
# editá .env según tu setup

# 4. Correr el agente
wso
# o:
python -m wso.main
```

## Roadmap

**v1 (en curso):**
- [ ] Loop agéntico con budget y continuación
- [ ] Parser XML streaming tolerante
- [ ] Tools de filesystem (read, write, delete, list)
- [ ] Tools de control de flujo (responder, preguntar)
- [ ] Sistema de permisos completo
- [ ] Cliente Ollama
- [ ] UI terminal con Rich

**v2:**
- [ ] `run_python` con sandbox
- [ ] Tools de Office (PPT, Excel)
- [ ] Tools de LinkedIn / scraping
- [ ] Clientes Anthropic, OpenAI, Google
- [ ] Persistencia de historial entre sesiones

## Notas de diseño

> "Este sistema debe priorizar la privacidad absoluta. Ningún dato de
> White Suit Studio sale de la red local salvo configuración explícita
> a un provider cloud."

Ver `blueprint.md` para el documento de diseño original.
