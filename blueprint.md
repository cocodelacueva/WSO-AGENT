---

# 📄 Blueprint: White Suit Operator (WSO)
**Versión:** 1.0 (Local Agent Prototype)

---

## 1. Concepto de "Harness" Personal
El **WSO** es un agente de terminal que utiliza la potencia de tu **Ryzen 9** para replicar la funcionalidad de *Claude Cowork* sin costos de tokens. Su función no es solo chatear, sino actuar sobre el sistema de archivos y ejecutar herramientas de productividad.

---

## 2. Requerimientos de Entorno
Para arrancar mañana, asegurate de tener esto listo en tu terminal:

```bash
# Motores de IA
ollama pull qwen2.5-coder:32b
ollama pull llama3.3:70b

# Dependencias Python
pip install ollama python-pptx pandas openpyxl playwright rich
playwright install chromium
```

---

## 3. Estructura de Trabajo
Diseñaremos el script para que gestione estas carpetas:
*   `/context`: Archivos `.md` con info de tus 15 devs y proyectos de juegos.
*   `/tools`: Scripts de automatización (PPT, Excel, LinkedIn).
*   `/output`: Donde el agente depositará los entregables finales.

---

## 4. El Loop Lógico (Python Pseudocode)
Este es el corazón del programa que vamos a codear:

```python
import ollama
import subprocess

def agent_loop(user_input):
    # 1. Cargar contexto de White Suit Studio
    context = open("context/studio_info.md").read()
    
    # 2. Consultar a Ollama
    response = ollama.chat(model='qwen2.5-coder:32b', messages=[
        {'role': 'system', 'content': 'Eres un agente de ejecución. Usa bloques <execute> para correr Python.'},
        {'role': 'user', 'content': f"Contexto: {context}\nPedido: {user_input}"}
    ])
    
    # 3. Parsear y Ejecutar si hay código
    if "<execute>" in response['message']['content']:
        # Extraer código y ejecutar localmente
        # Capturar el resultado y devolverlo a la IA para validación
        pass
```

---

## 5. Tareas Prioritarias (Mañana)
1.  **Módulo de Office:** Crear la función que convierta texto plano en `.pptx` automáticamente.
2.  **Módulo LinkedIn:** Configurar el "scraper" y redactor de posts basado en noticias del día.
3.  **Harness de Seguridad:** Definir en qué carpetas tiene permiso el agente para escribir/borrar.

---

## 6. Notas de Director
> "Este sistema debe priorizar la **privacidad absoluta**. Ningún dato de White Suit Studio sale de la red local. El modelo Qwen se encargará del código pesado y Llama 3.3 de la estrategia y comunicación."

---

**¡Listo! Mañana cuando estés frente a la compu, decime por dónde querés empezar (si por el motor de ejecución o por la integración de los archivos de contexto) y escribimos el código juntos.**