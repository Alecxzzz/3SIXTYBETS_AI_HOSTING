"""
IA de Soporte - modelo APARTE del ecosistema principal.

Modulo autocontenido: NO importa ni comparte codigo ni configuracion con
36AI (ai.ia36) ni con Demian (ai.model / engine.search_engine). Su unica
funcion es responder el chat de soporte al usuario final con un modelo
DEDICADO, distinto de los que usan las IAs principales.

Configuracion (variables de entorno):
  SUPPORT_AI_API_KEY   key del proveedor. Opcional: si falta reaprovecha
                       AI36_GROQ_API_KEY o GROQ_API_KEY (misma cuenta Groq,
                       pero modelo DISTINTO al de las IAs principales).
  SUPPORT_AI_MODEL     modelo dedicado de soporte. Default: qwen/qwen3.6-27b
                       (familia distinta a los gpt-oss de las IAs principales).
  SUPPORT_AI_BASE_URL  endpoint de chat completions (Groq por defecto).
  SUPPORT_AI_MAX_TOKENS / SUPPORT_AI_TIMEOUT / SUPPORT_AI_MAX_REINTENTOS

La respuesta del modelo SIEMPRE se limpia antes de devolverse: sin
razonamiento <think>, sin markdown (asteriscos, numerales, backticks)
y sin JSON crudo (fallas tipicas de modelos pequenos como qwen3).
"""

import json
import os
import re
import time

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


API_KEY = (
    os.getenv("SUPPORT_AI_API_KEY")
    or os.getenv("AI36_GROQ_API_KEY")
    or os.getenv("GROQ_API_KEY")
    or ""
)
BASE_URL = os.getenv("SUPPORT_AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
MODELO = os.getenv("SUPPORT_AI_MODEL", "qwen/qwen3.6-27b")
# 1024: qwen3 gasta tokens en razonamiento antes de responder; con menos
# valor la respuesta se trunca a mitad del razonamiento y no hay respuesta.
MAX_TOKENS = int(os.getenv("SUPPORT_AI_MAX_TOKENS", "1024"))
TIMEOUT = int(os.getenv("SUPPORT_AI_TIMEOUT", "45"))
MAX_REINTENTOS = int(os.getenv("SUPPORT_AI_MAX_REINTENTOS", "3"))

# Etiquetas de razonamiento que qwen3 mete DENTRO del content (a diferencia
# de gpt-oss, que las manda en un campo aparte). Concatenadas para evitar
# cualquier ambiguedad al escribirlas.
THINK_OPEN = "<" + "think" + ">"
THINK_CLOSE = "<" + "/" + "think" + ">"


def limpiar_razonamiento(content: str) -> str:
    """Quita el razonamiento del contenido visible.

    - Bloques cerrados (THINK_OPEN ... THINK_CLOSE): se eliminan.
    - Bloque SIN cerrar (respuesta truncada): se conserva solo lo que
      venga despues del ultimo bloque; si no hay nada, queda vacio.
    """
    if not content:
        return ""
    content = re.sub(
        re.escape(THINK_OPEN) + r".*?" + re.escape(THINK_CLOSE),
        "",
        content,
        flags=re.DOTALL,
    )
    if THINK_OPEN in content:
        content = content.split(THINK_OPEN)[-1]
    return content.strip()


# Campos tipicos donde un modelo mete el texto cuando responde con JSON.
_CLAVES_TEXTO = ("respuesta", "mensaje", "message", "text", "content")


def _extraer_texto_de_json(texto: str) -> str:
    """Si el modelo respondio con JSON/diccionario crudo, extrae el texto.

    Los modelos pequenos (qwen3) a veces imitan el formato JSON del prompt
    (ej. el bloque de DATOS REALES) y responden {"respuesta": "..."} en vez
    de texto conversacional. Esto lo convierte en texto plano.
    """
    candidato = texto.strip()
    if not candidato.startswith(("{", "[")):
        return texto
    datos = None
    try:
        datos = json.loads(candidato)
    except ValueError:
        match = re.search(r"\{.*\}", texto, flags=re.DOTALL)
        if match:
            try:
                datos = json.loads(match.group(0))
            except ValueError:
                datos = None
    if datos is None:
        # JSON truncado o malformado: rescatar el campo de texto con regex
        # (comilla final opcional: la respuesta puede cortarse a mitad)
        m = re.search(
            r'"(?:' + "|".join(_CLAVES_TEXTO) + r')"\s*:\s*"([^"]{3,})"?', texto
        )
        if m:
            return m.group(1).strip()
        return texto
    if isinstance(datos, dict):
        for clave in _CLAVES_TEXTO:
            valor = datos.get(clave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        for valor in datos.values():
            if isinstance(valor, str) and len(valor.strip()) >= 10:
                return valor.strip()
    if isinstance(datos, list):
        for item in datos:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for valor in item.values():
                    if isinstance(valor, str) and valor.strip():
                        return valor.strip()
    return texto


def limpiar_respuesta(texto: str) -> str:
    """Limpieza final para el usuario: sin razonamiento, sin markdown y
    sin JSON crudo. Texto conversacional plano, listo para el chat.
    """
    if not texto:
        return ""
    texto = limpiar_razonamiento(texto)
    texto = _extraer_texto_de_json(texto)
    texto = texto.replace("*", "").replace("#", "").replace("`", "")
    return texto.strip()


def responder(prompt: str):
    """Una sola llamada al modelo dedicado: soporte debe ser rapido.

    Sin herramientas, sin agente y SIN fallback hacia los modelos de las
    IAs principales (a proposito: el soporte no debe competir por los
    mismos buckets de tasa que 36AI). Devuelve texto limpio o None.
    """
    if not API_KEY:
        print("[Soporte] Sin API key (define SUPPORT_AI_API_KEY).")
        return None

    payload = {
        "model": MODELO,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.7,
        # qwen3 en Groq: manda el razonamiento a un campo aparte y deja el
        # content limpio. Si el modelo no lo soporta (400), se reintenta
        # sin el parametro y limpia limpiar_razonamiento.
        "reasoning_format": "parsed",
    }

    for _intento in range(MAX_REINTENTOS):
        try:
            r = requests.post(
                BASE_URL,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            print(f"[Soporte] Error de conexion: {exc}")
            time.sleep(2)
            continue

        if r.status_code == 200:
            try:
                content = r.json()["choices"][0]["message"]["content"] or ""
            except Exception:
                content = ""
            return limpiar_respuesta(content) or None

        if r.status_code == 400 and "reasoning_format" in r.text:
            payload.pop("reasoning_format", None)
            continue

        if r.status_code == 429:
            # Saturado: espera corta y reintenta con el MISMO modelo dedicado.
            # Si persiste, responder() devuelve None y soporte_chat cae al
            # aviso de WhatsApp (NO se usan los modelos de las IAs principales).
            print(f"[Soporte] 429 (saturado) en intento {_intento + 1}/{MAX_REINTENTOS} con {MODELO}")
            time.sleep(6)
            continue

        if r.status_code in (500, 502, 503):
            time.sleep(2)
            continue

        # 400/404/etc: el modelo dedicado no esta disponible. A proposito
        # NO se cae a los modelos principales.
        print(f"[Soporte] ERROR {r.status_code}: {r.text[:200]}")
        return None

    return None
