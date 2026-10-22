"""
Dashboard AI - generador de picks del dashboard.

Modulo AUTOCONTENIDO e independiente de las demas IAs del ecosistema. Existe
porque cada superficie necesita su propio motor: si el dashboard y el chat
compartieran modelo, una oleada de trafico en el chat dejaria el dashboard
mudo (y al reves). Groq aplica limites POR MODELO, asi que separarlos sube el
margen real de forma casi gratuita.

Configuracion (variables de entorno):
  DASHBOARD_AI_API_KEY   key del proveedor. Admite VARIAS separadas por comas:
                         se rota a la siguiente cuando una cuenta agota cuota.
                         Opcional: si falta usa AI36_GROQ_API_KEY / GROQ_API_KEY.
  DASHBOARD_AI_API_KEY_FALLBACK
                         claves extra del MISMO proveedor (alias comodo para
                         pegar una key de respaldo sin tocar la principal).
  DASHBOARD_AI_MODEL     modelo dedicado del dashboard.
  DASHBOARD_AI_FALLBACK  modelo de respaldo si el principal falla o da 404.
  DASHBOARD_AI_BASE_URL  endpoint de chat completions (formato OpenAI).
  DASHBOARD_AI_MAX_TOKENS / _TIMEOUT / _MAX_REINTENTOS

RESPALDO DE PROVEEDOR (opcional pero muy recomendado)
  El plan gratuito se agota POR CUENTA, no por modelo: cuando la cuenta se
  queda a cero TODOS los modelos devuelven 402/429 y rotar modelos no sirve.
  Por eso existe un segundo PROVEEDOR al que se salta solo en ese caso:
  DASHBOARD_AI_RESPALDO=off para desactivarlo (por defecto ON si hay key).
  DASHBOARD_AI_RESPALDO_API_KEY        (default: AI36_GROQ_API_KEY / GROQ_API_KEY)
  DASHBOARD_AI_RESPALDO_BASE_URL       (default: endpoint de Groq)
  DASHBOARD_AI_RESPALDO_MODEL          (default: openai/gpt-oss-120b)
  DASHBOARD_AI_RESPALDO_MODELOS_EXTRA  (default: openai/gpt-oss-20b,qwen/qwen3.8-27b)
  DASHBOARD_AI_PAUSA_KEY               segundos que una clave agotada queda
                                       fuera de rotacion (default 6 h)

CONFIGURACION DE BUSQUEDA WEB

El dashboard genera picks, asi que necesita informacion FRESCA (lesiones,
cuotas, forma reciente). El motor de chat no comparte esta busqueda: para que
el pick se apoye en datos frescos, el modulo busca en la web ANTES de generar
y le pasa los resultados al modelo como contexto.

Busqueda: ai.ia36.buscar_web (DuckDuckGo, ya anade mes y anio a la consulta).
"""

import json
import os
import time

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


# Consultas que se lanzan antes de pedir el pick. Se centran en lo que mas
# cambia entre partidos y no se puede deducir de la tabla de posiciones.
CONSULTAS_BUSQUEDA = [
    "{partido} lesiones y bajas hoy",
    "{partido} cuotas y pronosticos",
    "{partido} forma reciente ultimos partidos",
]
RESULTADOS_POR_BUSQUEDA = 3
MAX_CHARS_BUSQUEDA = int(os.getenv("DASHBOARD_AI_MAX_CHARS_BUSQUEDA", "2500"))


from ai.claves import claves_openrouter


def _claves(*valores) -> list:
    """Claves sin repetir. Cada valor admite varias separadas por coma."""
    salida, vistos = [], set()
    for valor in valores:
        for clave in (valor or "").split(","):
            clave = clave.strip()
            if clave and clave not in vistos:
                vistos.add(clave)
                salida.append(clave)
    return salida


BASE_URL = os.getenv(
    "DASHBOARD_AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"
)
MODELO = os.getenv("DASHBOARD_AI_MODEL", "openai/gpt-oss-20b")
MODELO_FALLBACK = os.getenv("DASHBOARD_AI_FALLBACK", "openai/gpt-oss-120b")
# Rotacion extra de modelos: Groq aplica el limite de TPD POR MODELO, asi que
# cuando uno se agota (429) el siguiente de la lista sigue teniendo margen.
# Antes, al agotarse gpt-oss-20b y gpt-oss-120b, el dashboard se quedaba mudo
# hasta el dia siguiente.
MODELOS_EXTRA = [
    m.strip()
    for m in os.getenv(
        "DASHBOARD_AI_MODELOS_EXTRA",
        # En OpenRouter los qwen de Groq devuelven 404 y solo suman latencia:
        # si no se configura nada, la cadena se queda con principal + fallback.
        "" if "openrouter.ai" in BASE_URL else "qwen/qwen3.8-27b",
    ).split(",")
    if m.strip()
]

# --------------------------------------------------------------------------
# ROTACION DE CLAVES Y DE PROVEEDOR
# --------------------------------------------------------------------------
# El plan gratuito se agota POR CUENTA: OpenRouter, por ejemplo, da 50
# peticiones diarias a modelos :free y, cuando la cuenta llega a cero, TODOS
# los modelos responden 429 ("free-models-per-day"). Rotar modelos en ese
# escenario no sirve de nada y el dashboard se quedaba sin picks hasta el dia
# siguiente. Se resuelve en dos niveles:
#   1. varias CLAVES del mismo proveedor (otra cuenta -> otra cuota),
#   2. un PROVEEDOR DE RESPALDO distinto (Groq por defecto), porque su cuota
#      es independiente de la de OpenRouter.
# Cada clave agotada queda en pausa un tiempo (no se reintenta en cada pick).
# La clave de respaldo incorporada (otra cuenta de OpenRouter) se suma SOLO si
# el proveedor principal es OpenRouter: su cuota es POR CUENTA, asi que una
# segunda clave devuelve 50 peticiones/dia extra justo cuando la primera dice
# "free-models-per-day". Contra Groq esa clave solo produciria un 401.
KEYS_PRINCIPAL = _claves(
    os.getenv("DASHBOARD_AI_API_KEY"),
    os.getenv("DASHBOARD_AI_API_KEY_FALLBACK"),
    ",".join(claves_openrouter()) if "openrouter.ai" in BASE_URL else "",
) or _claves(os.getenv("AI36_GROQ_API_KEY"), os.getenv("GROQ_API_KEY"))

RESPALDO_ACTIVO = os.getenv("DASHBOARD_AI_RESPALDO", "true").strip().lower() != "off"
RESPALDO_BASE_URL = os.getenv(
    "DASHBOARD_AI_RESPALDO_BASE_URL",
    "https://api.groq.com/openai/v1/chat/completions",
)
RESPALDO_MODELO = os.getenv("DASHBOARD_AI_RESPALDO_MODEL", "openai/gpt-oss-120b")
RESPALDO_MODELOS_EXTRA = [
    m.strip()
    for m in os.getenv(
        "DASHBOARD_AI_RESPALDO_MODELOS_EXTRA", "openai/gpt-oss-20b,qwen/qwen3.8-27b"
    ).split(",")
    if m.strip()
]
KEYS_RESPALDO = (
    _claves(
        os.getenv("DASHBOARD_AI_RESPALDO_API_KEY"),
        os.getenv("AI36_GROQ_API_KEY"),
        os.getenv("GROQ_API_KEY"),
    )
    if RESPALDO_ACTIVO
    else []
)

# Se conserva el nombre historico: otros modulos importan API_KEY.
API_KEY = KEYS_PRINCIPAL[0] if KEYS_PRINCIPAL else (KEYS_RESPALDO[0] if KEYS_RESPALDO else "")

# Clave -> instante hasta el que no se vuelve a usar. 6 h por defecto: los
# limites diarios de los planes gratuitos resetean a medianoche UTC, asi que
# reintentar antes solo gasta una peticion para redescubrir el 429.
KEYS_EN_PAUSA = {}
PAUSA_KEY_S = int(os.getenv("DASHBOARD_AI_PAUSA_KEY", str(6 * 3600)))


MAX_TOKENS = int(os.getenv("DASHBOARD_AI_MAX_TOKENS", "2000"))
TIMEOUT = int(os.getenv("DASHBOARD_AI_TIMEOUT", "60"))
MAX_REINTENTOS = int(os.getenv("DASHBOARD_AI_MAX_REINTENTOS", "3"))
REASONING_EFFORT = os.getenv("DASHBOARD_AI_REASONING_EFFORT", "low")

# Cooldown POR MODELO cuando Groq responde 429 (TPD/RPM agotado). Sin esto, el
# generador reintentaba 3 veces en cada uno de los ~90 partidos de la ventana:
# cientos de llamadas que solo gastan cuota y ralentizan el ciclo entero.
# Es por modelo y no global porque Groq descuenta el limite diario de forma
# independiente para cada uno: pausar todo dejaria fuera modelos con cuota.
MODELOS_EN_PAUSA = {}
PAUSA_429_S = int(os.getenv("DASHBOARD_AI_PAUSA_429", "900"))

# CONTADOR DE TOKENS POR DIA. Los 429 son reactivos: solo avisan cuando ya se
# agoto el TPD, y para entonces el dia ya esta perdido (nadie ve picks). Este
# contador es proactivo: al acercarse al limite se reserve una parte para las
# ULTIMAS horas del dia, que son las de mayor trafico, en vez de quemarla en
# las primeras horas. Es lo que hace que el sistema "aguante presion".
PRESUPUESTO_TPD = int(os.getenv("DASHBOARD_AI_TPD", "190000"))
# % del presupuesto que se puede gastar antes de las 20:00 (hora local del
# servidor, UTC en el hosting) para dejar margen a la punta de trafico.
RESERVA_PUNTA = float(os.getenv("DASHBOARD_AI_RESERVA_PUNTA", "0.75"))
HORA_PUNTA = int(os.getenv("DASHBOARD_AI_HORA_PUNTA", "20"))
# Si el TPD real del plan es mayor, se ajusta con DASHBOARD_AI_TPD.

_consumo = {"dia": "", "tokens": 0, "llamadas": 0}

# ULTIMO ERROR DE LA IA. Cuando el dashboard deja de generar picks el motivo
# (404 de un modelo retirado, 402 de la cuenta, 413 del ITPM...) solo estaba en
# los logs del hosting. Se guarda aqui para verlo desde /dashboard/salud.
_ultimo_error = {"detalle": "", "ts": 0.0}


# REGULADOR DE ITPM (tokens de entrada por minuto). Los modelos qwen de Groq
# aceptan 7000 tokens/min de entrada. El generador dispara peticiones seguidas
# (un pick, y hasta 3 reintentos) y se pasa ese limite: Groq responde 413 y el
# el ciclo se queda sin picks. Aqui se lleva la cuenta de lo enviado en la
# ventana de un minuto y se espera antes de salir, en vez de comerse el error.
ITPM_LIMITE = int(os.getenv("DASHBOARD_AI_ITPM", "6000"))
ITPM_VENTANA_S = 60
_itpm = {"marca": 0.0, "tokens": 0}


def _itpm_registrar(tokens: int) -> None:
    ahora = time.time()
    if ahora - _itpm["marca"] > ITPM_VENTANA_S:
        _itpm["marca"] = ahora
        _itpm["tokens"] = 0
    _itpm["tokens"] += max(0, int(tokens or 0))


def _itpm_espera(payload: dict) -> float:
    """Segundos que hay que esperar para no pasarse del limite de ITPM.

    0 significa que todavia hay margen (no hay que esperar).
    """
    largo = len(json.dumps(payload, ensure_ascii=False)) // 3  # ~chars->tokens
    if _itpm["tokens"] + largo > ITPM_LIMITE:
        restante = ITPM_VENTANA_S - (time.time() - _itpm["marca"])
        return max(0.0, restante)
    _itpm_registrar(largo)
    return 0.0



def _reset_consumo() -> None:
    hoy = time.strftime("%Y-%m-%d")
    if _consumo["dia"] != hoy:
        _consumo.update(dia=hoy, tokens=0, llamadas=0)


def presupuesto_disponible() -> int:
    """Tokens que aún se pueden gastar hoy sin comerse la reserva de la punta."""
    _reset_consumo()
    tpm = PRESUPUESTO_TPD
    if time.localtime().tm_hour < HORA_PUNTA:
        tpm = int(tpm * RESERVA_PUNTA)
    return max(0, tpm - _consumo["tokens"])


def registrar_consumo(tokens: int) -> None:
    _reset_consumo()
    _consumo["tokens"] += max(0, int(tokens or 0))
    _consumo["llamadas"] += 1


def estado_consumo() -> dict:
    """Consumo del dia (para /dashboard/salud)."""
    _reset_consumo()
    hace = (
        int(time.time() - _ultimo_error["ts"]) if _ultimo_error["ts"] else None
    )
    return {
        "tokens_hoy": _consumo["tokens"],
        "llamadas_hoy": _consumo["llamadas"],
        "presupuesto": PRESUPUESTO_TPD,
        "disponible": presupuesto_disponible(),
        "ultimo_error": _ultimo_error["detalle"][:300],
        "ultimo_error_hace_s": hace,
    }


def _modelo_en_pausa(modelo: str) -> bool:
    return time.time() < MODELOS_EN_PAUSA.get(modelo, 0.0)


def _marcar_cuota_agotada(modelo: str, motivo: str) -> None:
    MODELOS_EN_PAUSA[modelo] = time.time() + PAUSA_429_S
    print(
        f"[Dashboard-AI] Limite del proveedor agotado en {modelo} ({motivo}); "
        f"pausa {PAUSA_429_S}s para ese modelo",
        flush=True,
    )


def _etiqueta_clave(clave: str) -> str:
    """Identificador corto de una clave, nunca la clave completa."""
    if len(clave) <= 12:
        return "(clave corta)"
    return f"{clave[:10]}...{clave[-4:]}"


def _clave_en_pausa(clave: str) -> bool:
    return time.time() < KEYS_EN_PAUSA.get(clave, 0.0)


def _marcar_clave_agotada(clave: str, motivo: str) -> None:
    """Saca la clave de la rotacion: su CUENTA no puede seguir gastando."""
    KEYS_EN_PAUSA[clave] = time.time() + PAUSA_KEY_S
    print(
        f"[Dashboard-AI] Cuenta sin cuota ({_etiqueta_clave(clave)}): "
        f"{motivo}; se rota de clave durante {PAUSA_KEY_S}s",
        flush=True,
    )


# Senales de que el problema es de la CUENTA (no del modelo ni del minuto):
# hay que cambiar de clave, porque reintentar con otro modelo dara lo mismo.
# NO se incluyen marcas genericas de rate limit ni "quota exceeded": en Groq
# esos limites son POR MODELO y rotar de clave solo perderia una cuenta sana.
MARCAS_CUENTA_AGOTADA = (
    "free-models-per-day",
    "insufficient_quota",
    "insufficient credits",
    "add credits",
    "credit balance",
    "prepaid credit balance",
    "insufficient funds",
    "payment_required",
    "depleted",
    "key limit exceeded",
    "no auth credentials",
    "invalid api key",
    "unauthorized",
    "payment required",
)


def _cuenta_agotada(status_code: int, cuerpo: str) -> bool:
    """True si la respuesta indica que la CUENTA (no el modelo) no puede pagar.

    401/402 son siempre de credencial/cuota. El 429 se mira por texto porque el
    mismo codigo sirve para "te pasaste este minuto" (reintentar sirve) y para
    "se acabo el cupo del dia" (reintentar no sirve).
    """
    texto = (cuerpo or "").lower()
    if status_code in (401, 402):
        return True
    if status_code == 403 and "key limit exceeded" in texto:
        return True
    if status_code != 429:
        return False
    return any(marca in texto for marca in MARCAS_CUENTA_AGOTADA)


def _perfiles() -> list:
    """Proveedores en orden de preferencia, con sus claves y modelos.

    Se deduplica por (endpoint, clave) para no repetir la misma llamada cuando
    el respaldo acaba siendo el mismo proveedor y la misma cuenta.
    """
    perfiles, vistos = [], set()

    def _anadir(nombre, base_url, claves, modelos):
        if not base_url or not claves:
            return
        claves = [k for k in claves if (base_url, k) not in vistos]
        for k in claves:
            vistos.add((base_url, k))
        if claves:
            perfiles.append(
                {
                    "nombre": nombre,
                    "base_url": base_url,
                    "claves": claves,
                    "modelos": modelos,
                }
            )

    _anadir("principal", BASE_URL, KEYS_PRINCIPAL, _cadena_modelos())
    _anadir(
        "respaldo",
        RESPALDO_BASE_URL,
        KEYS_RESPALDO,
        _cadena_modelos(RESPALDO_MODELO, RESPALDO_MODELOS_EXTRA),
    )
    return perfiles


def estado_claves() -> list:
    """Estado de las claves SIN exponerlas (para /dashboard/salud)."""
    ahora = time.time()
    salida = []
    for perfil in _perfiles():
        for clave in perfil["claves"]:
            hasta = KEYS_EN_PAUSA.get(clave, 0.0)
            salida.append(
                {
                    "proveedor": perfil["nombre"],
                    "endpoint": perfil["base_url"].split("//")[-1].split("/")[0],
                    "clave": _etiqueta_clave(clave),
                    "en_pausa": hasta > ahora,
                    "reanuda_en_s": max(0, int(hasta - ahora)) if hasta > ahora else 0,
                    # Modelos que se probaran con ESTA clave. Sin esto, un
                    # DASHBOARD_AI_RESPALDO_MODEL mal escrito solo se descubre
                    # por los 404 de los logs.
                    "modelos": list(perfil["modelos"]),
                }
            )
    return salida


def claves_disponibles() -> int:
    """Claves que aun pueden usarse (ni agotadas ni en pausa)."""
    return sum(
        1
        for perfil in _perfiles()
        for clave in perfil["claves"]
        if not _clave_en_pausa(clave)
    )

DEBUG = os.getenv("DASHBOARD_AI_DEBUG", "false").lower() == "true"

# max_tokens POR MODELO. El limite OTPM (tokens de SALIDA por minuto) de Groq
# va de 1000 a 20000 segun el modelo: pedir mas de lo que el modelo puede
# servir por minuto devuelve 429 aunque quede cuota de dia. Un pick es un JSON
# de ~200 tokens, asi que 2000 era ademas un desperdicio. Estos valores estan
# por debajo del OTPM de cada modelo con margen para la respuesta.
MAX_TOKENS_POR_MODELO = {
    "qwen/qwen3.8-27b": 600,     # OTPM 1000
    "allallam-2-7b": 600,
}


def _max_tokens_para(modelo: str) -> int:
    return MAX_TOKENS_POR_MODELO.get(modelo, MAX_TOKENS)


def dashboard_configurado() -> bool:
    """True si hay al menos una clave usable (principal o de respaldo)."""
    return bool(_perfiles())


def buscar_contexto_web(mensaje: str) -> str:
    """Busca en la web lo que un pick necesita y devuelve el contexto.

    Reutiliza ai.ia36.buscar_web (DuckDuckGo) porque ya anade mes y anio a la
    consulta, que es lo que evita que el modelo se apoye en datos de temporadas
    pasadas. Se degrada a cadena vacia si la busqueda falla: es preferible
    generar el pick con menos contexto que no generar nada.
    """
    if os.getenv("DASHBOARD_AI_WEB", "true").lower() != "true":
        return ""

    try:
        from ai.ia36 import buscar_web
    except Exception as exc:
        print(f"[Dashboard-AI] busqueda web no disponible: {exc}", flush=True)
        return ""

    # El "mensaje" del dashboard suele traer el partido; se recorta para no
    # lanzar consultas gigantes que devuelven nada util.
    partido = (mensaje or "").strip()[:120]
    if not partido:
        return ""

    bloques = []
    for plantilla in CONSULTAS_BUSQUEDA:
        consulta = plantilla.format(partido=partido)
        try:
            resultado = buscar_web(consulta, max_resultados=RESULTADOS_POR_BUSQUEDA)
        except Exception as exc:
            resultado = f"(error: {exc})"
        if resultado and "Error" not in resultado[:20]:
            bloques.append(f"=== {consulta} ===\n{resultado}")

    if not bloques:
        return ""

    contexto = "\n\n".join(bloques)
    if len(contexto) > MAX_CHARS_BUSQUEDA:
        contexto = contexto[:MAX_CHARS_BUSQUEDA] + "\n[contexto recortado]"
    return contexto


def _extraer_texto(data) -> str:
    """Saca el content de la respuesta estilo OpenAI."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return (message.get("content") or "").strip()


def _construir_payload(
    modelo: str, system_prompt: str, mensaje: str, base_url: str = ""
) -> dict:
    """Payload OpenAI-compatible. `base_url` decide el dialecto de razonamiento.

    Se pasa como parametro (y no se lee del global) porque el proveedor de
    respaldo puede ser otro distinto al principal: OpenRouter usa `reasoning`
    y Groq `reasoning_effort`.
    """
    endpoint = base_url or BASE_URL
    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje},
        ],
        "max_tokens": _max_tokens_para(modelo),
    }
    if "openrouter.ai" in endpoint:
        # Los modelos de razonamiento de OpenRouter consumen max_tokens antes de
        # emitir texto. "low" acota el gasto sin perder calidad en un JSON corto.
        payload["reasoning"] = {"effort": REASONING_EFFORT}
    elif modelo.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = REASONING_EFFORT
    return payload


def _cadena_modelos(modelo: str = "", extras=None) -> list:
    """Modelos a probar en orden, sin repetir.

    Groq descuenta el limite diario POR MODELO, asi que rotar es lo que
    mantiene vivo el dashboard cuando un modelo agota su TPD.
    """
    if modelo:
        cadena = [modelo, *(extras or [])]
    else:
        cadena = [MODELO, MODELO_FALLBACK, *MODELOS_EXTRA]
    vistos, out = set(), []
    for m in cadena:
        if m and m not in vistos:
            vistos.add(m)
            out.append(m)
    return out


def _headers(clave: str, base_url: str) -> dict:
    """Cabeceras para una clave concreta del perfil indicado."""
    headers = {
        "Authorization": f"Bearer {clave}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in (base_url or ""):
        # OpenRouter pide identificarse; sin esto algunas rutas dan 403.
        headers["HTTP-Referer"] = os.getenv(
            "OPENROUTER_HTTP_REFERER", "https://threesixtybets.com"
        )
        headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "3SIXTYBETS Dashboard")
    return headers


def generar_picks(system_prompt: str, mensaje: str, buscar_web: bool = True):
    """Genera el texto del pick con el modelo DEDICADO del dashboard.

    Antes de preguntar al modelo se hace una busqueda web (lesiones, cuotas,
    forma) y el resultado se inyecta como contexto, para que el pick se apoye
    en datos frescos y no en memoria del modelo. Si la busqueda falla, se sigue
    adelante sin ella: es preferible un pick con menos contexto que ninguno.

    Recorre proveedor principal -> respaldo y, dentro de cada uno, clave por
    clave y modelo por modelo. Devuelve (texto, nombre_modelo) o (None, None)
    si no se pudo.
    """
    perfiles = _perfiles()
    if not perfiles:
        print("[Dashboard-AI] Sin API key (define DASHBOARD_AI_API_KEY).")
        _ultimo_error.update(
            detalle="sin API key configurada (DASHBOARD_AI_API_KEY)", ts=time.time()
        )
        return None, None

    ultimo_error = ""

    # Contexto web (una sola vez, compartido por modelo principal y fallback).
    # Las VERIFICACIONES la desactivan: ya se le pide al modelo que contraste
    # con fuentes externas y repetir 3 busquedas web por cada pasada multiplicaba
    # el gasto de tokens y el tiempo del ciclo sin aportar datos nuevos.
    contexto_web = buscar_contexto_web(mensaje) if buscar_web else ""
    if DEBUG:
        print(f"[Dashboard-AI] contexto web: {len(contexto_web)} chars", flush=True)

    system_con_contexto = system_prompt
    if contexto_web:
        system_con_contexto = (
            system_prompt
            + "\n\nCONTEXTO DE BUSQUEDA WEB (datos frescos, es la fuente de "
            "verdad; no inventes datos que no aparezcan aqui):\n"
            + contexto_web
        )

    for perfil in perfiles:
        base_url = perfil["base_url"]
        for clave in perfil["claves"]:
            if _clave_en_pausa(clave):
                continue
            headers = _headers(clave, base_url)
            cuenta_agotada = False

            for modelo in perfil["modelos"]:
                if not modelo or _modelo_en_pausa(modelo):
                    continue
                if presupuesto_disponible() <= 0:
                    # Se gasto el presupuesto del dia (o su parte reservada). Se
                    # corta aqui en vez de esperar a que el 429 lo tire todo: es
                    # preferible dejar de generar AHORA que quedarse mudo.
                    print(
                        f"[Dashboard-AI] Presupuesto diario agotado "
                        f"({estado_consumo()['tokens_hoy']}/{PRESUPUESTO_TPD} tokens)",
                        flush=True,
                    )
                    return None, None
                payload = _construir_payload(
                    modelo, system_con_contexto, mensaje, base_url
                )

                for intento in range(max(1, MAX_REINTENTOS)):
                    # Espera preventiva ANTES de enviar: mejor ralentizar el
                    # ciclo que recibir un 413 y perder el pick. Solo espera si
                    # la ventana de ITPM del modelo ya esta llena.
                    espera = _itpm_espera(payload)
                    if espera > 0:
                        print(
                            f"[Dashboard-AI] {modelo}: ITPM al limite, esperando "
                            f"{espera:.0f}s antes de enviar",
                            flush=True,
                        )
                        time.sleep(min(espera + 1, 70))
                    try:
                        r = requests.post(
                            base_url, headers=headers, json=payload, timeout=TIMEOUT
                        )
                    except requests.exceptions.RequestException as exc:
                        ultimo_error = f"conexion: {exc}"
                        time.sleep(2)
                        continue

                    if r.status_code == 200:
                        # Contar lo que DE VERDAD costo la llamada (prompt +
                        # respuesta): es lo que descuenta el proveedor.
                        try:
                            _uso = r.json().get("usage") or {}
                            registrar_consumo(int(_uso.get("total_tokens") or 0))
                        except Exception:
                            registrar_consumo(0)
                        texto = _extraer_texto(r.json())
                        if texto:
                            if DEBUG:
                                print(
                                    f"[Dashboard-AI] {modelo} respondio "
                                    f"({len(texto)} chars)"
                                )
                            _ultimo_error.update(detalle="", ts=0.0)
                            return texto, modelo
                        # content vacio aqui SI es fallo: este modulo no usa
                        # tools, asi que no puede ser una peticion de
                        # herramienta. Es el razonamiento del gpt-oss consumiendo
                        # el presupuesto de max_tokens.
                        ultimo_error = f"{modelo} devolvio content vacio"
                        if DEBUG:
                            print(f"[Dashboard-AI] {modelo}: vacio, subiendo max_tokens")
                        # Se sube el techo de salida, pero nunca por encima del
                        # OTPM del modelo: pedir mas de lo que puede servir por
                        # minuto devolvia 429 y se perdia la respuesta.
                        payload["max_tokens"] = min(_max_tokens_para(modelo) * 2, 8000)
                        time.sleep(1)
                        continue

                    ultimo_error = f"{modelo} {r.status_code}: {r.text[:200]}"
                    if DEBUG:
                        print(f"[Dashboard-AI] {ultimo_error}", flush=True)

                    # CUENTA SIN CUOTA (402/401, o 429 de cupo diario): no tiene
                    # sentido probar otro modelo con la misma clave, porque el
                    # limite es de la cuenta. Se marca la clave y se pasa a la
                    # siguiente (o al proveedor de respaldo).
                    if _cuenta_agotada(r.status_code, r.text):
                        _marcar_clave_agotada(clave, f"{r.status_code}: {r.text[:120]}")
                        cuenta_agotada = True
                        break

                    # 413 en Groq = ITPM (limite de tokens de ENTRADA POR
                    # MINUTO del modelo, 7000 en los qwen), NO de ventana de
                    # contexto: el prompt mide ~1000 tokens y aun asi lo rechaza
                    # porque se han enviado demasiados tokens en el minuto. Asi
                    # que no se recorta (el prompt ya es pequeno) sino que se
                    # ESPERA a que se libere la ventana.
                    if r.status_code == 413:
                        espera = _itpm_espera(payload)
                        if espera > 0:
                            ultimo_error = (
                                f"{modelo}: ITPM saturado, esperando {espera:.0f}s"
                            )
                            print(f"[Dashboard-AI] {ultimo_error}", flush=True)
                            time.sleep(min(espera, 65))
                            payload = _construir_payload(
                                modelo, system_con_contexto, mensaje, base_url
                            )
                            continue
                        # La ventana no se libera: este modelo no aguanta el ritmo.
                        _marcar_cuota_agotada(modelo, "ITPM: no aguanta nuestro ritmo")
                        break

                    if r.status_code == 429:
                        # Limite de cuota/rate DEL MODELO: no insistir con el
                        # mismo modelo ni reintentar 3 veces (agota el cupo del
                        # resto del dia). Se pasa al siguiente modelo.
                        _marcar_cuota_agotada(modelo, r.text[:120])
                        break
                    if r.status_code in (500, 502, 503):
                        time.sleep(min(2 ** intento * 2, 8))
                        continue
                    if (
                        r.status_code == 400
                        and "too large" in r.text.lower()
                        and contexto_web
                    ):
                        # Prompt mas largo que la ventana del modelo: se
                        # reintenta recortando el contexto web. Antes ese 400
                        # agotaba el modelo y se perdia el pick aunque otro mas
                        # grande respondiera.
                        contexto_web = ""
                        payload = _construir_payload(
                            modelo, system_prompt, mensaje, base_url
                        )
                        ultimo_error = f"{modelo}: prompt recortado por exceeded context"
                        continue
                    # 404 (modelo retirado) u otro error: siguiente modelo.
                    break

                if cuenta_agotada:
                    break  # siguiente clave del mismo perfil

    if claves_disponibles() == 0:
        print(
            "[Dashboard-AI] Todas las claves estan sin cuota; se reintentaran "
            "automaticamente cuando expire la pausa.",
            flush=True,
        )
    print(f"[Dashboard-AI] sin respuesta: {ultimo_error}", flush=True)
    _ultimo_error.update(
        detalle=ultimo_error or "sin detalle del proveedor", ts=time.time()
    )
    return None, None
