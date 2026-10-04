"""
Deteccion centralizada de fallos de PROVEEDOR de IA.
=====================================================
Unificar el diagnostico de "este proveedor no sirve ahora" estaba duplicado en
tres sitios (main.py, dashboard.py, ai/model.py) y cada copia con su propia lista
de marcas. Cuando un proveedor cambia el texto de su error, el filtro viejo deja
de reconocerlo y el fallo se cuela como si fuera una respuesta valida: en el
caso del dashboard eso terminaba en un descarte con motivo 'sin_datos', que
BORRA el partido durante 6 horas y hace creer que a la IA le faltan datos
cuando en realidad se acabo la cuota.

Este modulo es la unica fuente de verdad. Las listas de marcas son broad a
proposito: preferimos clasificar de mas (caer al motor de relevo, que es
gratis) que de menos (guardar basura como si fuera una respuesta).
"""


# --- Marcas de error comunes a cualquier proveedor -------------------------
# Fallos de creditos / cuota / suscripcion.
MARCAS_SIN_CUOTA = (
    "payment_required",
    "prepaid credit balance",
    "add credits",
    "depleted",
    "insufficient funds",
    "quota exceeded",
    "rate_limit",          # el proveedor lo escribe asi en el cuerpo
    # You.com al expirar el plan de prueba:
    "trial has ended",
    "platform trial",
    "upgrade to get access",
    # Groq:
    "requests per day",
    "tokens per day",
    "rate limit",
    # Prefijos que ya armaba el motor de busqueda propio:
    "error de you.com (402",
    "error de you.com (429",
    "no se pudo completar la busqueda en vivo",
)

# Fallos transitorios: reintentar mas tarde tiene sentido.
MARCAS_TRANSITORIAS = (
    "timeout", "timed out", "read timed out",
    "rate_limit", "too many requests",
    "503", "502", "500",
    "service unavailable", "bad gateway",
    "temporarily unavailable", "overloaded",
    "connection reset", "connection aborted",
    "remote end closed", "connection error",
)

# Respuestas que NO son un fallo del proveedor sino una negativa del modelo.
# NO deben disparar el relevo: son respuestas validas ("no tengo datos").
MARCAS_SIN_DATOS = (
    "no tengo datos", "sin datos", "no encuentro informacion",
    "insufficient information", "i don't have data",
    "not enough information",
)


def sin_cuota(texto) -> bool:
    """True si la respuesta indica que el proveedor NO puede seguir (saldo/cuota).

    Cubre el caso real: el proveedor devuelve su mensaje de error DENTRO de un
    200 OK, asi que no se puede detectar por codigo HTTP, solo por texto.
    """
    if not texto:
        return False
    t = str(texto).lower()
    return any(m in t for m in MARCAS_SIN_CUOTA)


def transitorio(texto) -> bool:
    """True si el fallo se Ira solo (red, rate limit). Vale la pena reintentar."""
    if not texto:
        return False
    t = str(texto).lower()
    return any(m in t for m in MARCAS_TRANSITORIAS)


def respuesta_nula(texto) -> bool:
    """True si la respuesta esta vacia o es un error del proveedor, no una pick.

    Unificacion de lo que cada modulo hacia por su cuenta:
      - _es_error_ia (dashboard.py)
      - _you_fallo_de_creditos (main.py)
      - generar_respuesta_you (ai/model.py)

    OJO: NO incluye las marcas de 'sin datos'. Un {"error": "sin datos"} es una
    respuesta VALIDA del modelo (el filtro anti-alucinaciones), no una caida del
    proveedor: tratarla como error haria que reintentara infinitamente el mismo
    partido y, al final, lo descartaria igual.
    """
    if not texto:
        return True
    t = str(texto).strip()
    if not t:
        return True
    bajo = t.lower()
    if bajo.startswith((
        "error", "error:", "error de you.com", "error leyendo",
        "demian tipster no pudo", "no se pudo",
    )):
        return True
    # Fallos del proveedor embebidos en un 200 OK (el caso de 'trial has ended').
    return sin_cuota(bajo) or transitorio(bajo)


def clasificar(texto) -> str:
    """Clasifica la respuesta en: 'ok' | 'sin_cuota' | 'transitorio' | 'nulo'."""
    if not str(texto or "").strip():
        return "nulo"
    if sin_cuota(texto):
        return "sin_cuota"
    if transitorio(texto):
        return "transitorio"
    if respuesta_nula(texto):
        return "nulo"
    return "ok"
