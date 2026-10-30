# White Suit Operator — WSO

Agente de programación de terminal, al estilo Claude Code. Corre contra
modelos locales (Ollama) o cloud (Anthropic, OpenAI y compatibles) con la
misma interfaz, y está pensado para andar bien con **poca VRAM** (16 GB).

**Versión:** 0.4.0 ("rin"): solo programación, tool calling nativo.

---

## Uso

```bash
cd ~/proyectos/mi-app     # el proyecto sobre el que querés trabajar
wso
```

WSO trabaja sobre la carpeta donde lo abrís: lee, busca, edita archivos y
corre comandos ahí. Si el proyecto tiene un `WSO.md` (o `AGENTS.md` /
`CLAUDE.md`), lo carga como instrucciones del proyecto: comandos de test,
convenciones, cosas a evitar.

Comandos del REPL: `/reset` borra el historial, `/exit` sale.
Ctrl+C interrumpe el turno en curso (y mata el comando que esté corriendo).

## Setup

Requiere Python 3.11+.

### Instalación (una sola vez)

```bash
cd /ruta/a/wso-ai-harness
python3 -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[all]"
cp .env.example .env                 # y editalo (ver abajo)
```

Usá siempre `python -m pip` con el venv activado, no `pip` a secas: así
se instala en el Python del venv y no en el global (pyenv, sistema).
Para reinstalar cuando cambien dependencias, repetí el `python -m pip
install -e ".[all]"` desde la carpeta del harness con el venv activado.

### Arrancar

WSO se abre **desde la carpeta del proyecto en el que querés trabajar**,
no desde la del harness. La config se lee igual del `.env` del harness.

**Opción A: alias (recomendado).** Agregá al final de `~/.zshrc`:

```bash
alias wso=/ruta/a/wso-ai-harness/.venv/bin/wso
```

Recargá con `source ~/.zshrc`. Desde ahí, en cualquier carpeta:

```bash
cd ~/proyectos/mi-app
wso
```

**Opción B: enlace en el PATH (sin alias ni activar nada).** El script
`.venv/bin/wso` ya apunta al Python del venv, así que alcanza con un
enlace en una carpeta que esté en el PATH:

```bash
mkdir -p ~/.local/bin
ln -s /ruta/a/wso-ai-harness/.venv/bin/wso ~/.local/bin/wso
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc   # una sola vez
source ~/.zshrc
which wso        # → ~/.local/bin/wso
```

Si movés la carpeta del harness, rehacé el enlace.

> ¿Por qué no agregar `.venv/bin` directo al PATH? Funciona, pero esa
> carpeta también tiene `python`, `pip`, `pytest`, `ruff`… del venv: pasarían
> a ser los de toda tu terminal y taparían los de pyenv/sistema en otros
> proyectos. El enlace expone solo `wso`.

**Opción C: activando el venv.**

```bash
source /ruta/a/wso-ai-harness/.venv/bin/activate
cd ~/proyectos/mi-app
wso
```

### Si `wso` falla con `ModuleNotFoundError: No module named 'wso'`

La terminal está ejecutando una copia vieja de `wso` instalada en el
Python global en vez de la del venv. Verificá con `which wso`: tiene que
terminar en `.venv/bin/wso`. Si no:

```bash
# sacar la copia del Python global (ajustá la ruta a la que muestre `which wso`)
/ruta/al/python/global -m pip uninstall -y wso
hash -r          # que zsh olvide la ruta cacheada
which wso        # ahora debería apuntar al venv
```

O usá el alias de la opción A, que apunta directo al venv.

### Modelo local (Ollama)

```env
WSO_MODE=local
WSO_LOCAL_URL=http://localhost:11434   # o la IP de la PC con la GPU
WSO_LOCAL_MODEL=qwen2.5-coder:14b
WSO_LOCAL_NUM_CTX=16384
```

El modelo tiene que soportar **tool calling** en Ollama. Los de la familia
Qwen (2.5-coder, 3) lo soportan. Probá varios: la diferencia entre modelos
para trabajo agéntico es grande.

**Contexto y VRAM (16 GB, modelo 14B en Q4 ≈ 9 GB de pesos):**

| `num_ctx` | KV cache | Total aprox. |
|---|---|---|
| 8192 | f16 | ~11 GB (queda muy justo para programar) |
| **16384** | f16 | **~12.5 GB** |
| 32768 | q8_0 | ~12.5 GB |

Para 32k, en el servidor de Ollama:
`OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`. Verificá con
`ollama ps` que diga `100% GPU`: si derrama a CPU, se vuelve lentísimo.

WSO mantiene el historial por debajo de `num_ctx` (Ollama recorta en
silencio si te pasás): resume resultados de tools viejos y, si hace falta,
descarta turnos viejos. Vas a ver un aviso `[contexto: …]` cuando pasa, y
el uso de contexto al final de cada turno.

### Cloud

**Anthropic:**

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=anthropic
WSO_CLOUD_MODEL=claude-opus-5-5
ANTHROPIC_API_KEY=sk-ant-...
```

Usa prompt caching (cada paso del loop paga solo lo nuevo) y thinking
adaptive visible. Para modelos viejos que no soportan thinking/effort:
`WSO_ANTHROPIC_THINKING=false` y `WSO_ANTHROPIC_EFFORT=` (vacío).

**OpenAI y compatibles** (OpenRouter, Groq, Gemini, vLLM, LM Studio…):

```env
WSO_MODE=cloud
WSO_CLOUD_PROVIDER=openai
WSO_CLOUD_MODEL=gpt-4.1
OPENAI_API_KEY=sk-...
# WSO_OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

## Tools

| Tool | Qué hace | Permiso |
|---|---|---|
| `read_file` | Lee un archivo con líneas numeradas, por tramos (offset/limit) | auto dentro del proyecto |
| `glob` | Busca archivos por patrón de nombre | auto dentro del proyecto |
| `grep` | Busca regex en el contenido (usa `rg` si está instalado) | auto dentro del proyecto |
| `edit_file` | Reemplaza un fragmento exacto de un archivo | pide aprobación (muestra diff) |
| `write_file` | Crea o reescribe un archivo | pide aprobación (muestra diff) |
| `bash` | Corre un comando en el proyecto (tests, git, linters…) | pide aprobación salvo comandos de solo lectura |

Recomendado: instalá [ripgrep](https://github.com/BurntSushi/ripgrep)
(`brew install ripgrep`) para que `grep` sea rápido y respete `.gitignore`.

## Permisos

Cuando una acción necesita aprobación ves el diff (o el comando) y elegís:

| Código | Efecto |
|---|---|
| `y` | Aprobar esta vez |
| `s` | Aprobar por la sesión: **todas las ediciones del proyecto**, o comandos con el mismo prefijo (`pytest`, `git commit`…) |
| `a` | Aprobar siempre (se guarda en `config/permissions.toml`) |
| `n` / Enter | Rechazar y cortar el turno |
| texto | Rechazar y decirle al agente qué hacer en cambio |

Se auto-aprueban sin preguntar: lecturas dentro del proyecto y comandos de
solo lectura (`ls`, `git status`, `git diff`, `rg`, …) que no tocan paths
fuera del proyecto. Un prefijo aprobado nunca cubre comandos encadenados
(`pytest && rm -rf x` siempre pregunta).

## Logs e historial

```env
WSO_LOG_ENABLED=true        # logs/session_*.jsonl con cada evento (debug, auditoría)
WSO_HISTORY_PERSIST=true    # restaura la conversación al reabrir wso
```

## Desarrollo

```bash
pytest -q          # tests
ruff check wso tests
```

Ver `DESIGN.md` para la arquitectura y el porqué de cada decisión.

> "Este sistema debe priorizar la privacidad absoluta. Ningún dato de
> White Suit Studio sale de la red local salvo configuración explícita
> a un provider cloud."
