"""
36AI - Analista cuantitativo de apuestas deportivas (Groq + tools).

Motor agéntico del ecosistema 3SIXTYBETS: razona con un modelo de Groq y
decide cuándo buscar contexto en internet (forma, lesiones, H2H, clima) y
cuándo consultar cuotas reales del partido, todo vía function-calling.

Toda la configuración vive en variables de entorno (AI36_*). No usa claves
hardcodeadas en producción: si falta la key, el flujo degrada con un mensaje
claro en vez de cortar el servidor.
"""

import os
import re
import json
import time
from datetime import datetime

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - depende del entorno
    DDGS = None


# ============================================================
# CONFIGURACIÓN (todo desde entorno, con defaults seguros)
# ============================================================

# Si el entorno no define la key, se usa la key proporcionada por el usuario
# para que la IA funcione out-of-the-box. En producción sobreescribir con AI36_GROQ_API_KEY.
GROQ_API_KEY = os.getenv("AI36_GROQ_API_KEY") or os.getenv("GROQ_API_KEY") or ""
ODDS_API_KEY = os.getenv("AI36_ODDS_API_KEY") or ""

GROQ_URL = os.getenv("AI36_GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODELS_URL = os.getenv("AI36_GROQ_MODELS_URL", "https://api.groq.com/openai/v1/models")
ODDS_URL = os.getenv("AI36_ODDS_URL", "https://odds-api.io/api/v1/odds")

MODELO_DEFAULT = os.getenv("AI36_GROQ_MODEL", "openai/gpt-oss-120b")
MODELO_FALLBACK = os.getenv("AI36_GROQ_FALLBACK", "llama-3.3-70b-versatile")

MODELOS_PREFERIDOS = [
    os.getenv("AI36_GROQ_MODEL", "openai/gpt-oss-120b"),
    "openai/gpt-oss-120b",
]

MAX_TOKENS = int(os.getenv("AI36_MAX_TOKENS", "2500"))
MAX_CHARS_HERRAMIENTA = int(os.getenv("AI36_MAX_CHARS_HERRAMIENTA", "600"))
MAX_CHARS_MENSAJES = int(os.getenv("AI36_MAX_CHARS_MENSAJES", "12000"))

MAX_ITERACIONES = int(os.getenv("AI36_MAX_ITERACIONES", "6"))
ITERACION_FORZAR_RESPUESTA = int(os.getenv("AI36_ITERACION_FORZAR", "4"))
MAX_REINTENTOS = int(os.getenv("AI36_MAX_REINTENTOS", "4"))

# Debug: cambiar a True para ver qué devuelve el modelo
DEBUG = os.getenv("AI36_DEBUG", "false").lower() == "true"


# ============================================================
# DEFINICIÓN DE HERRAMIENTAS
# ============================================================

tools = [
    {
        "type": "function",
        "function": {
            "name": "buscar_web",
            "description": "Busca contexto en internet: forma reciente, lesiones, alineaciones, clima, historial H2H.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta de búsqueda específica."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_cuotas",
            "description": "Busca cuotas decimales reales de un partido específico entre dos equipos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "equipo_local": {"type": "string"},
                    "equipo_visitante": {"type": "string"},
                    "deporte": {"type": "string", "description": "ej: soccer, baseball, basketball"}
                },
                "required": ["equipo_local", "equipo_visitante"]
            }
        }
    }
]



# ============================================================
# UTILIDADES
# ============================================================

def truncar_texto(texto, max_chars=MAX_CHARS_HERRAMIENTA):
    if not texto:
        return texto
    if len(texto) <= max_chars:
        return texto
    return texto[:max_chars - 3] + "..."


def limpiar_respuesta(content):
    """Extrae SOLO el formato EDGE de la respuesta.

    Busca en TODO el contenido, incluyendo dentro de bloques de razonamiento
    que algunos modelos (gpt-oss) emiten entre etiquetas think.
    """
    if not content:
        return content

    think_open = "<" + "think" + ">"
    think_close = "</" + "think" + ">"

    # 1. Buscar el patrón EDGE en TODO el contenido (incluyendo dentro de bloques think)
    pattern = r'Partido:.*?Confianza del pick:\s*\d+%'
    matches = list(re.finditer(pattern, content, re.DOTALL | re.IGNORECASE))
    if matches:
        match = matches[-1]  # Última coincidencia = respuesta real
        resultado = match.group(0).strip()
        # Incluir advertencia después del porcentaje
        after_match = content[match.end():]
        extra_lines = after_match.strip().split('\n')[:3]
        warning_lines = []
        for line in extra_lines:
            line = line.strip()
            if not line:
                continue
            if any(w in line.lower() for w in ['menor', 'favor', 'revisar', '60%']):
                warning_lines.append(line)
            else:
                break
        if warning_lines:
            resultado += '\n\n' + '\n'.join(warning_lines)
        return "🧠 EDGE DETECTADO\n\n" + resultado

    # 2. Buscar el marcador 🧠 EDGE DETECTADO en todo el contenido
    marker = "🧠 EDGE DETECTADO"
    last_idx = content.rfind(marker)
    if last_idx >= 0:
        after_marker = content[last_idx:]
        conf_match = re.search(r'Confianza del pick:\s*\d+%', after_marker)
        if conf_match:
            end_idx = conf_match.end()
            remaining = after_marker[end_idx:].strip().split('\n')[:3]
            warning_lines = []
            for line in remaining:
                line = line.strip()
                if not line:
                    continue
                if any(w in line.lower() for w in ['menor', 'favor', 'revisar', '60%']):
                    warning_lines.append(line)
                else:
                    break
            result = after_marker[:end_idx]
            if warning_lines:
                result += '\n\n' + '\n'.join(warning_lines)
            return result.strip()
        return after_marker.strip()

    # 3. Si no hay patrón ni marcador, devolver contenido sin bloques think
    cleaned = re.sub(re.escape(think_open) + r'.*?' + re.escape(think_close), '', content, flags=re.DOTALL).strip()
    if cleaned:
        return cleaned

    # 4. Si todo estaba dentro de bloques think, extraer de ahí
    think_match = re.search(re.escape(think_open) + r'(.*?)' + re.escape(think_close), content, re.DOTALL)
    if think_match:
        inner = think_match.group(1).strip()
        if inner:
            return inner




def buscar_web(query, max_resultados=3):
    """Búsqueda web vía DuckDuckGo (ddgs)."""
    if DDGS is None:
        return "Búsqueda web no disponible (falta el paquete ddgs)."
    try:
        with DDGS() as ddgs:
            resultados = list(ddgs.text(query, max_results=max_resultados))
        if not resultados:
            return "Sin resultados."
        texto = "\n".join(f"- {r.get('title', '')}: {r.get('body', '')}" for r in resultados)
        return truncar_texto(texto)
    except Exception as e:
        return f"Error en la búsqueda: {e}"


def buscar_cuotas(equipo_local, equipo_visitante, deporte=None):
    """Cuotas decimales reales vía odds-api.io."""
    if not ODDS_API_KEY:
        return "Consulta de cuotas no configurada (falta AI36_ODDS_API_KEY)."
    try:
        params = {"apiKey": ODDS_API_KEY}
        if deporte:
            params["sport"] = deporte
        r = requests.get(ODDS_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        eventos = data if isinstance(data, list) else data.get("data", [])
        texto = ""
        for evento in eventos:
            home = str(evento.get("home_team", "")).lower()
            away = str(evento.get("away_team", "")).lower()
            if equipo_local.lower() in home or equipo_local.lower() in away or \
               equipo_visitante.lower() in home or equipo_visitante.lower() in away:
                texto += f"Partido: {evento.get('home_team')} vs {evento.get('away_team')}\n"
                for book in evento.get("bookmakers", []):
                    texto += f"  Casa: {book.get('title')}\n"
                    for market in book.get("markets", []):
                        for outcome in market.get("outcomes", []):
                            texto += f"    {outcome.get('name')}: {outcome.get('price')}\n"
        return truncar_texto(texto) if texto else "No se encontraron cuotas para este partido."
    except Exception as e:
        return f"Error consultando cuotas: {e}"


def compactar_messages(messages, max_chars=MAX_CHARS_MENSAJES):
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    if total_chars <= max_chars:
        return messages
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    resto = messages[1:] if system_msg else messages[:]
    max_system = int(max_chars * 0.6)
    if system_msg:
        sys_content = str(system_msg.get("content", ""))
        if len(sys_content) > max_system:
            half = max_system // 2
            system_msg = dict(system_msg)
            system_msg["content"] = sys_content[:half] + "\n...[resumido]...\n" + sys_content[-half:]
    system_chars = len(str(system_msg.get("content", ""))) if system_msg else 0
    espacio = max_chars - system_chars
    mantener = []
    acum = 0
    for msg in reversed(resto):
        mc = len(str(msg.get("content", "")))
        if acum + mc > espacio:
            restante = espacio - acum
            if restante > 100:
                msg = dict(msg)
                msg["content"] = str(msg.get("content", ""))[:restante - 3] + "..."
                mantener.insert(0, msg)
            break
        mantener.insert(0, msg)
        acum += mc
    resultado = []
    if system_msg:
        resultado.append(system_msg)
    resultado.extend(mantener)
    return resultado




# ============================================================
# SELECCIÓN DE MODELO Y LLAMADA A GROQ
# ============================================================

def obtener_modelos_disponibles():
    if not GROQ_API_KEY:
        return []
    try:
        response = requests.get(
            url=GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return [m["id"] for m in data.get("data", [])]
        return []
    except Exception:
        return []


def seleccionar_modelo():
    """Elige el primer modelo preferido disponible; si no, uno de chat cualquiera."""
    disponibles = obtener_modelos_disponibles()
    if not disponibles:
        if DEBUG:
            print("[36AI] No se pudieron consultar modelos. Usando por defecto:", MODELO_DEFAULT)
        return MODELO_DEFAULT
    modelos_chat = [
        m for m in disponibles
        if not any(x in m.lower() for x in ["guard", "whisper", "tts", "moderation", "distil", "prompt-guard"])
    ]
    for preferido in MODELOS_PREFERIDOS:
        if preferido in modelos_chat:
            return preferido
    if modelos_chat:
        if DEBUG:
            print("[36AI] Ningún modelo preferido disponible. Usando:", modelos_chat[0])
        return modelos_chat[0]
    if DEBUG:
        print("[36AI] No hay modelos de chat disponibles. Usando:", MODELO_DEFAULT)
    return MODELO_DEFAULT


def llamar_modelo(messages, max_reintentos=MAX_REINTENTOS, usar_tools=True, modelo_actual=None, max_tokens=None):
    """Llama a Groq chat completions con reintentos, fallback de modelo y compactación."""
    modelo = modelo_actual or seleccionar_modelo()
    payload = {
        "model": modelo,
        "messages": messages,
        "max_tokens": max_tokens or MAX_TOKENS
    }
    if usar_tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    ultimo_error = "Error desconocido"

    for intento in range(max_reintentos):
        try:
            response = requests.post(
                url=GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=60
            )
        except requests.exceptions.RequestException as e:
            ultimo_error = f"Error de conexión: {e}"
            time.sleep(3)
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception:
                ultimo_error = f"Respuesta no es JSON válido: {response.text[:300]}"
                time.sleep(3)
                continue
            if "choices" in data:
                if DEBUG:
                    print(f"[36AI][DEBUG] Respuesta: {json.dumps(data['choices'][0]['message'], ensure_ascii=False)[:500]}")
                return data, modelo
            else:
                ultimo_error = f"Respuesta sin 'choices': {json.dumps(data)[:500]}"
                time.sleep(3)
                continue

        try:
            detalle = response.json()
            ultimo_error = f"{response.status_code}: {detalle.get('error', {}).get('message', response.text)}"
        except Exception:
            ultimo_error = f"{response.status_code}: {response.text or '(sin cuerpo de error)'}"

        if response.status_code == 429:
            time.sleep(20)
            continue
        if response.status_code == 413:
            messages = compactar_messages(messages)
            payload["messages"] = messages
            time.sleep(2)
            continue
        if response.status_code in (500, 502, 503):
            time.sleep(2 ** intento * 3)
            continue

        if "tool" in ultimo_error.lower() or "function" in ultimo_error.lower():
            # 1) Intentar modelo de respaldo si el actual no soporta herramientas
            if modelo != MODELO_FALLBACK:
                modelo = MODELO_FALLBACK
                payload["model"] = modelo
                if usar_tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                if DEBUG:
                    print(f"[36AI] El modelo no soporta herramientas. Cambiando a: {MODELO_FALLBACK}")
                time.sleep(2)
                continue
            # 2) Ya estamos en el respaldo: quitar herramientas y reintentar
            if usar_tools:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
                usar_tools = False
                time.sleep(2)
                continue

        if DEBUG:
            print(f"[36AI] Error: {ultimo_error}")
        return None, modelo

    if DEBUG:
        print(f"[36AI] Se agotaron los reintentos. Último error: {ultimo_error}")
    return None, modelo




# ============================================================
# LÓGICA PRINCIPAL DEL ANÁLISIS
# ============================================================

def analizar_36ai(mensaje_usuario, system_prompt):
    """Ejecuta el loop agéntico de 36AI y devuelve el análisis en formato EDGE."""
    if not GROQ_API_KEY:
        return "36AI no está configurada. Falta AI36_GROQ_API_KEY en el backend."

    try:
        fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    except Exception:
        fecha_hoy = ""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"[Fecha actual: {fecha_hoy}] Analiza: {mensaje_usuario}"}
    ]

    modelo_en_uso = None

    for iteracion in range(MAX_ITERACIONES):
        forzar_respuesta = iteracion >= ITERACION_FORZAR_RESPUESTA

        if forzar_respuesta and iteracion == ITERACION_FORZAR_RESPUESTA:
            messages.append({
                "role": "user",
                "content": "Ya no busques más información. Con los datos que tienes hasta ahora, "
                           "da tu análisis final siguiendo el formato solicitado."
            })

        messages = compactar_messages(messages)
        data, modelo_en_uso = llamar_modelo(
            messages, usar_tools=not forzar_respuesta, modelo_actual=modelo_en_uso
        )

        if data is None:
            return None

        mensaje = data['choices'][0]['message']
        tool_calls = mensaje.get("tool_calls")
        content = mensaje.get("content")

        # Si el modelo devuelve contenido, limpiarlo y usarlo como respuesta
        if content and content.strip():
            limpio = limpiar_respuesta(content)
            if limpio:
                return limpio
            if DEBUG:
                print(f"[36AI][DEBUG] limpiar_respuesta devolvió vacío. Content original: {content[:200]}")
            return content.strip()

        # Si hay tool_calls, procesarlos
        if tool_calls and not forzar_respuesta:
            messages.append(mensaje)
            for tool_call in tool_calls:
                nombre_funcion = tool_call['function']['name']
                try:
                    args = json.loads(tool_call['function']['arguments'])
                except json.JSONDecodeError:
                    args = {}

                if nombre_funcion == "buscar_web":
                    resultado = buscar_web(args.get("query", ""))
                elif nombre_funcion == "buscar_cuotas":
                    resultado = buscar_cuotas(
                        args.get("equipo_local", ""),
                        args.get("equipo_visitante", ""),
                        args.get("deporte")
                    )
                else:
                    resultado = "Herramienta desconocida."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call['id'],
                    "content": resultado
                })

            time.sleep(1.5)
            continue

        # Si estamos forzando respuesta pero el modelo devolvió tool_calls sin contenido,
        # pedir explícitamente que NO use herramientas
        if tool_calls and forzar_respuesta:
            messages.append({
                "role": "user",
                "content": "No uses más herramientas. Con los datos que ya tienes, "
                           "responde directamente tu análisis en el formato solicitado."
            })
            continue

        # Si no hay contenido ni tool_calls, pedir respuesta
        messages.append({
            "role": "user",
            "content": "Por favor, responde con tu análisis del partido en el formato solicitado."
        })
        continue

    return "No se pudo obtener una respuesta final."


def generar_respuesta_36ai(prompt_sistema: str, prompt_usuario: str) -> str:
    """Punto de entrada público compatible con ai.model.generar_respuesta."""
    return analizar_36ai(prompt_usuario, prompt_sistema) or ""


# ============================================================
# CLASIFICACIÓN: diferenciar conversación de análisis de partido
# ============================================================

_CHAT_TRIGGERS = (
    "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "hey", "hi", "hello", "gracias", "gracia", "ok", "okay", "vale",
    "que tal", "como estas", "como estás", "quien eres", "quién eres",
    "tu nombre", "ayuda", "help",
)


def _clasificar_rapida(mensaje: str):
    """Clasificación rápida por regex. Devuelve una etiqueta o None si es incierta."""
    texto = mensaje.strip()
    if not texto:
        return "CONVERSACION"
    t = texto.lower()

    # Match claro: vs / versus / v. / contra
    if re.search(r'\b(vs|versus|v\.|contra)\b', t):
        return "SPORTS_MATCH"

    # Saludos / charla corta conocida
    if len(t) <= 40 and any(t == w or t.startswith(w + " ") or t.startswith(w + ",") or t.startswith(w + "!") for w in _CHAT_TRIGGERS):
        return "CONVERSACION"

    return None


def clasificar_36ai(mensaje: str) -> str:
    """Decide si el mensaje pide analizar un partido (SPORTS_MATCH) o es conversación.

    Usa un filtro rápido por regex y, si es incierto, una llamada ligera a Groq
    (sin tools, pocos tokens) para clasificar con precisión.
    """
    if not GROQ_API_KEY:
        # Sin Groq no podemos clasificar con modelo: usamos solo el regex.
        return _clasificar_rapida(mensaje) or "CONVERSACION"

    rapida = _clasificar_rapida(mensaje)
    if rapida:
        return rapida

    prompt_sistema = (
        "Clasifica el mensaje del usuario para 365AI, un asistente de apuestas deportivas.\n"
        "Responde SOLO una etiqueta, nada más:\n\n"
        "SPORTS_MATCH = el usuario pide analizar, pronosticar o dar un pick de un partido o "
        "enfrentamiento CONCRETO entre dos equipos o jugadores (con o sin la palabra 'vs').\n"
        "CONVERSACION = saludo, charla normal, o pregunta deportiva/apuestas GENERAL sin un "
        "partido concreto (ej: 'que es handicap asiatico', 'hola', 'que opinas de los Yankees', "
        "'como uso la IA', 'que mercado me recomiendas').\n\n"
        "Si dudas entre los dos, elige CONVERSACION."
    )
    messages = [
        {"role": "system", "content": prompt_sistema},
        {"role": "user", "content": f"Mensaje: {mensaje}\nEtiqueta:"},
    ]

    data, _ = llamar_modelo(messages, usar_tools=False, max_tokens=20)
    if not data:
        return "CONVERSACION"

    content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip().upper()
    if "SPORTS_MATCH" in content:
        return "SPORTS_MATCH"
    if "CONVERSACION" in content:
        return "CONVERSACION"
    return "CONVERSACION"


def responder_conversacion_36ai(mensaje: str, system_prompt: str) -> str:
    """Responde en modo conversación: una sola llamada a Groq, sin tools ni formato EDGE."""
    if not GROQ_API_KEY:
        return "365AI no está configurada. Falta AI36_GROQ_API_KEY en el backend."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": mensaje},
    ]
    data, _ = llamar_modelo(messages, usar_tools=False)
    if not data:
        return None

    content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
    # Quitar bloques de razonamiento internos que algunos modelos emiten
    content = re.sub(r'<' + 'think' + r'>.*?</' + 'think' + r'>', '', content, flags=re.DOTALL).strip()
    return content or None


def procesar_36ai(mensaje: str) -> str:
    """Punto de entrada principal de 365AI.

    Clasifica el mensaje:
    - SPORTS_MATCH  -> análisis agéntico con tools + formato EDGE.
    - CONVERSACION  -> respuesta natural de asistente, sin EDGE ni tools.
    """
    from engine.prompt_builder import construir_prompt_sistema_36ai, construir_prompt_conversacional

    tipo = clasificar_36ai(mensaje)

    if tipo == "SPORTS_MATCH":
        respuesta = analizar_36ai(mensaje, construir_prompt_sistema_36ai())
    else:
        respuesta = responder_conversacion_36ai(mensaje, construir_prompt_conversacional("365AI"))

    if respuesta:
        respuesta = respuesta.replace("*", "").replace("#", "")
    return respuesta or "365AI no pudo generar una respuesta. Intenta de nuevo."
