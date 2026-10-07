"""
DASHBOARD - Motor de pronosticos automaticos de 3SIXTYBETS.

Cada dia la IA genera picks para los partidos disponibles (todos los deportes)
usando SOLO los mercados definidos en MERCADOS_POR_DEPORTE. Reglas clave:
- Nunca repetir el mismo mercado en los diferentes/mismos partidos.
- Variar las opciones: no solo 1X2 o goles; buscar la apuesta mas facil de
  acertar con valor.
- Los picks se guardan en MySQL (tabla ai_picks) y se muestran en el dashboard.
- Al finalizar un partido se resuelve el pick (ACIERTO / FALLO). En el
  dashboard solo se muestran los ACERTADOS en el apartado de aciertos.
"""

import json
import os
import re
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta, timezone

import db


# Rango GOLDEN PICK (elite: 1-2 por dia, doble verificados)
ODDS_GOLDEN_MIN = 1.35
ODDS_GOLDEN_MAX = 1.40
# Rango GENERAL publicable (todos los demas picks del dia)
ODDS_MINIMA = 1.20
ODDS_MAXIMA = 2.50
# Compat con codigo que usa el rango golden
ODDS_MINIMA_GOLDEN = ODDS_GOLDEN_MIN
ODDS_MAXIMA_GOLDEN = ODDS_GOLDEN_MAX
TIER_GOLDEN = "GOLDEN PICK"
TIER_STANDARD = "STANDARD"
# Minimo de partidos a cubrir manana (la IA analiza hasta lograrlo)
MIN_JUEGOS_MANANA = 15
# Doble verificacion antes de publicar: 2 pasadas independientes de la IA
# contra fuentes externas; AMBAS deben coincidir.
VERIFICACIONES_PUBLICAR = 2
# Los acertados del dia anterior se muestran hasta las 23:00 Nicaragua
# (la docstring de aciertos_visibles() ya decia 23:00; la constante estaba en
# 20 y ocultaba 3 horas extra de aciertos, dejando el panel casi vacio).
HORA_CORTE_ACERTADOS = 23
# La IA analiza/genera picks a cualquier hora (antes solo desde las 21:00).
# Se mantiene la constante por compatibilidad pero ya no bloquea.
HORA_INICIO_ANALISIS = 0
# Las 5 mejores ligas de Europa: se analizan PRIMERO (prioridad del dashboard).
LIGAS_TOP_EUROPA = ("eng.1", "esp.1", "ita.1", "ger.1", "fra.1")
# Ventana de analisis: partidos que arrancan dentro de las proximas N horas
# (los EN VIVO siempre entran). A las 9 PM las grandes ligas europeas juegan
# de madrugada/manana siguiente, asi que sin ventana no habria nada que
# analizar a esa hora.
VENTANA_ANALISIS_H = 32
# Verificacion estricta de aciertos: N pasadas independientes de la IA contra
# fuentes externas (Sofascore/Flashscore/Fotmob); TODAS deben coincidir.
VERIFICACIONES_ACIERTO = 6

TZ_NICARAGUA = db.TZ_NICARAGUA

# Salud del scheduler (idea 20: monitoreo; visible en /dashboard/salud)
_salud = {
    "ultimo_ciclo": None,
    "ultima_generacion_con_picks": None,
    "ultima_generacion_ts": 0.0,
    "ultimo_forzado_ts": 0.0,
    "generaciones_fallidas": 0,
    "sofascore_estado": "sin usar",
    "marcadores_discrepantes": 0,
    "archivo_ultimo_dia": None,
}


def _hora_nicaragua():
    return datetime.now(timezone.utc).astimezone(TZ_NICARAGUA)


def _es_golden(odds) -> bool:
    """True si la cuota cae en el rango elite GOLDEN (1.35-1.40)."""
    try:
        return ODDS_GOLDEN_MIN <= float(odds) <= ODDS_GOLDEN_MAX
    except (TypeError, ValueError):
        return False


def _cuota_valida(pick):
    """True si la cuota esta en el rango GENERAL publicable (1.20-2.50).

    Los GOLDEN (1.35-1.40) son solo 1-2 destacados; el resto de picks del
    dia se publica en el rango general.
    """
    odds = pick.get("odds")
    if odds is None:
        return False
    try:
        return ODDS_MINIMA <= float(odds) <= ODDS_MAXIMA
    except (TypeError, ValueError):
        return False


# Frases que delatan picks generados SIN datos reales (basura que no se muestra)
_FRASES_SIN_DATOS = (
    "sin datos", "no disponible", "no disponibles", "no especificad",
    "no identificad", "no verifiable", "sin cuota", "linea no disponible",
    "n/d", "no apostar", "prepick", "sin recomendacion", "sin recomendación",
)

_NOMBRES_INVALIDOS = {"", "?", "n/a", "na", "jugador a", "jugador b",
                      "equipo a", "equipo b", "local", "visitante",
                      "team a", "team b", "home", "away", "jugador 1", "jugador 2"}


def _nombre_valido(nombre) -> bool:
    return bool(nombre) and str(nombre).strip().lower() not in _NOMBRES_INVALIDOS


def _texto_sin_datos(texto) -> bool:
    t = (texto or "").lower()
    return any(f in t for f in _FRASES_SIN_DATOS)


def _pick_calidad_ok(pick: dict) -> bool:
    """Gate de calidad para MOSTRAR un pick (pendientes y aciertos).

    Rechaza: equipos '?', nombres genericos, sin cuota, cuota <= 1.20,
    rationale/titulo con frases de 'sin datos' y mercados prohibidos
    ('sin empate' / DNB). Es la ultima barrera: aunque un pick prohibido
    llegara a la BD, nunca se muestra en el dashboard.
    """
    if not (_nombre_valido(pick.get("homeName")) and _nombre_valido(pick.get("awayName"))):
        return False
    if not _cuota_valida(pick) or pick.get("odds") is None:
        return False
    if _texto_sin_datos(pick.get("rationale")) or _texto_sin_datos(pick.get("titulo")):
        return False
    if _texto_sin_datos(pick.get("eventName")):
        return False
    if _mercado_prohibido(pick.get("titulo"), pick.get("market"), pick.get("selection")):
        return False
    return True

# ============================================================
# CUOTAS REALES (odds-api.io)
# ---------------------------------------------------------------
# odds-api.io es la fuente SECUNDARIA: la principal es Doradobet
# (cuotas_doradobet.py), que es la que manda de verdad. Sin ODDS_API_KEY esta
# fuente se desactiva sola y el sitio sigue con Doradobet + ESPN.
#
# Antes imprimia un [WARN] en cada arranque y hacia una llamada HTTP por ciclo
# que siempre respondia 401: puro ruido que hacia creer que las cuotas del
# sistema eran estimadas, cuando NO lo son (Doradobet aporta las reales).
ODDS_API_KEY = os.getenv("ODDS_API_KEY") or os.getenv("AI36_ODDS_API_KEY") or ""
ODDS_API_BASE = "https://api.odds-api.io/v3"
# Plan free: solo 2 bookmakers permitidos por la cuenta: Bet365 y Winpot MX.
# (1xbet/Stake daban 403 "Access denied" y por eso faltaban cuotas reales.)
ODDS_BOOKMAKERS = "Bet365"
# Mapeo de nuestros deportes a los slugs de odds-api.io
ODDS_SPORT_SLUGS = {
    "soccer": "football",
    "nba": "basketball",
    "mlb": "baseball",
    "tennis": "tennis",
}

_odds_cache = {}  # clave -> (timestamp, data)
_ODDS_CACHE_TTL = 600  # 10 min para cuotas de un evento (respeta rate limit)
_ODDS_EVENTS_TTL = 1800  # 30 min para la lista de eventos (la cuota diaria es de 500 req)
_odds_bloqueado_hasta = 0  # backoff cuando la API responde 429


def _odds_bloqueado():
    return time.time() < _odds_bloqueado_hasta


def _norm_texto(s: str) -> str:
    s = (s or "").lower().strip()
    reemplazos = {
        "Ã¡": "a", "Ã©": "e", "Ã­": "i", "Ã³": "o", "Ãº": "u", "Ã¼": "u", "Ã±": "n",
    }
    for k, v in reemplazos.items():
        s = s.replace(k, v)
    return s


def _odds_request(path: str, params: dict):
    # Sin key no hay nada que pedir: antes se hacia la llamada igual y
    # respondia 401 en cada ciclo (ruido + un request inútil por ciclo).
    if not ODDS_API_KEY:
        return None
    if _odds_bloqueado():
        return None
    try:
        import requests

        params = {"apiKey": ODDS_API_KEY, **params}
        r = requests.get(f"{ODDS_API_BASE}{path}", params=params, timeout=20)
        if r.status_code != 200:
            # 401 (sin key valida) y 403 de bookmakers ya no se anuncian: son
            # fallos esperables de la fuente secundaria y solo ensuciaban el log.
            # El 429 SI se avisa, porque es un limite real de cuota.
            if r.status_code == 429:
                print(f"[Dashboard] odds-api.io {path} -> 429: limite alcanzado", flush=True)
                # 429 (limite diario/horario): pausar 15 min para no quemar cuota
                global _odds_bloqueado_hasta
                _odds_bloqueado_hasta = time.time() + 900
                return None
            # 403 de bookmakers: devolver los permitidos por la cuenta
            if r.status_code == 403 and "Allowed:" in r.text:
                permitidos = r.text.split("Allowed:", 1)[1]
                permitidos = permitidos.split(".")[0].strip()
                return {"__permitidos__": [p.strip() for p in permitidos.split(",")]}
            return None
        return r.json()
    except Exception as exc:
        print(f"[Dashboard] odds-api.io {path} error: {exc}")
        return None


def _bookmakers_permitidos(event_id=None):
    """Bookmakers seleccionados en la cuenta (free plan: max 2)."""
    return _odds_request("/odds", {"eventId": event_id, "bookmakers": ODDS_BOOKMAKERS})


def _odds_eventos(sport_slug: str):
    """Lista de eventos de un deporte, con cache de 30 min (ahorra cuota diaria)."""
    ahora = time.time()
    cached = _odds_cache.get(f"events:{sport_slug}")
    if cached and ahora - cached[0] < _ODDS_EVENTS_TTL:
        return cached[1]
    data = _odds_request("/events", {"sport": sport_slug})
    if isinstance(data, list):
        _odds_cache[f"events:{sport_slug}"] = (ahora, data)
        return data
    # Respuesta vacia/fallida: cache corto para no machacar la API
    _odds_cache[f"events:{sport_slug}"] = (ahora - _ODDS_CACHE_TTL + 60, [])
    return []


def _odds_evento(event_id):
    """Mercados reales del evento, con cache. Autodetecta los bookmakers
    permitidos por la cuenta si la seleccion inicial es rechazada (403)."""
    ahora = time.time()
    cached = _odds_cache.get(f"odds:{event_id}")
    if cached and ahora - cached[0] < _ODDS_CACHE_TTL:
        return cached[1]

    global ODDS_BOOKMAKERS
    books = ODDS_BOOKMAKERS
    mercados = None

    # Hasta 2 intentos: el 2º usa los bookmakers permitidos que reporte la API
    for _ in range(2):
        data = _odds_request("/odds", {"eventId": event_id, "bookmakers": books})
        if data and data.get("__permitidos__"):
            permitidos = [b for b in data["__permitidos__"] if b]
            if permitidos:
                books = ",".join(permitidos)
                ODDS_BOOKMAKERS = books  # recordar para las siguientes llamadas
                continue
        if isinstance(data, dict):
            for bookmaker in (data.get("bookmakers") or {}).values():
                if isinstance(bookmaker, list) and bookmaker:
                    mercados = bookmaker
                    break
        break

    # Solo cacheamos resultados validos; los fallidos se reintentan
    if mercados is not None:
        _odds_cache[f"odds:{event_id}"] = (ahora, mercados)
    return mercados


def _evento_oddsapi_con_mercados(sport: str, home_name: str, away_name: str):
    """Devuelve (evento, mercados, local_en_odds) de odds-api.io para el duelo.

    El matching es por PAR de equipos sin importar el orden (odds-api puede
    listar el mismo duelo invertido). local_en_odds=True si el local del pick
    es el 'home' de odds-api; False si esta invertido; None si no hubo match.
    """
    slug = ODDS_SPORT_SLUGS.get(sport)
    if not slug:
        return None, None, None

    def _coincide(a: str, b: str) -> bool:
        a, b = _norm_texto(a), _norm_texto(b)
        return bool(a) and bool(b) and (a == b or a in b or b in a)

    for e in _odds_eventos(slug):
        if e.get("status") not in ("pending", "live"):
            continue
        if _coincide(e.get("home", ""), home_name) and _coincide(
            e.get("away", ""), away_name
        ):
            return e, (_odds_evento(e.get("id")) or []), True
        if _coincide(e.get("home", ""), away_name) and _coincide(
            e.get("away", ""), home_name
        ):
            return e, (_odds_evento(e.get("id")) or []), False
    return None, None, None


def _cuotas_reales(sport: str, home_name: str, away_name: str) -> str:
    """Devuelve cuotas reales, priorizando el detalle completo de Doradobet."""
    try:
        from cuotas_doradobet import get_events_deporte, event_id_doradobet, detalle_mercado_para_ia

        data = get_events_deporte(sport)
        if data:
            evento = event_id_doradobet(sport, home_name, away_name)
            if evento:
                detalle = detalle_mercado_para_ia(evento)
                if detalle:
                    return detalle
    except Exception as exc:
        print(f"[Dashboard] Error leyendo detalle Doradobet: {exc}", flush=True)

    # Fallback existente: odds-api.io.
    evento, mercados, _local = _evento_oddsapi_con_mercados(sport, home_name, away_name)
    if not evento or not mercados:
        return ""

    lineas = [f"CUOTAS REALES ({evento['home']} vs {evento['away']}, odds-api.io):"]
    for m in mercados[:20]:
        nombre = m.get("name", "?")
        for o in m.get("odds", [])[:2]:
            pares = [f"{k}={v}" for k, v in o.items() if v not in (None, "")]
            lineas.append(f"- {nombre}: {', '.join(pares)}")
    lineas.append(
        "USA estas cuotas reales para calcular valor; la linea que elijas debe "
        "respetar los minimos del catalogo."
    )
    return "\n".join(lineas)


def _num_cuota(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 1.0 else None


def _cuota_real_pick(sport: str, home_name: str, away_name: str,
                     market: str, selection: str, titulo: str):
    """Cuota REAL (Bet365 via odds-api.io) para el mercado/seleccion del pick.

    Mapea el mercado del catalogo a los mercados de odds-api: 1X2->ML,
    sin empate->Draw No Bet, doble oportunidad->Double Chance,
    handicap->Spread, over/under->Totals/Goals O/U (incl. corners y
    tarjetas), ambos marcan->BTTS. Devuelve float o None si no existe.
    """
    _, mercados, local_en_odds = _evento_oddsapi_con_mercados(
        sport, home_name, away_name
    )
    if not mercados:
        return None
    # DORADOBET (Altenar) tiene prioridad: es la casa donde apuesta la gente.
    try:
        from cuotas_doradobet import cuota_doradobet

        cuota_dorado = cuota_doradobet(sport, home_name, away_name, market, selection, titulo)
        if cuota_dorado:
            return cuota_dorado
    except Exception as exc:
        print(f"[Dashboard] Error cuotas Doradobet: {exc}", flush=True)

    texto = f"{market} {titulo} {selection}".lower()
    nh, na = _norm_texto(home_name), _norm_texto(away_name)
    lado_txt = _norm_texto(f"{titulo} {selection}")

    def _mercado(*claves):
        for m in mercados:
            n = (m.get("name") or "").lower()
            if any(c in n for c in claves):
                return m
        return None

    def _lado():
        """Lado del pick en terminos de odds-api (reorienta si esta invertido)."""
        l = None
        for nombre in (nh, na):
            if not nombre:
                continue
            if nombre in lado_txt:
                l = "home" if nombre == nh else "away"
                break
            # nombre largo de odds-api ("real betis seville") vs corto del pick
            palabras = nombre.split()
            if len(palabras) >= 2 and " ".join(palabras[:2]) in lado_txt:
                l = "home" if nombre == nh else "away"
                break
        if l is None:
            return None
        if local_en_odds is False:
            return "away" if l == "home" else "home"
        return l

    # Sin empate (Draw No Bet)
    if "sin empate" in texto:
        m = _mercado("draw no bet")
        for o in (m or {}).get("odds") or []:
            if isinstance(o, dict):
                lado = _lado()
                if lado and _num_cuota(o.get(lado)):
                    return _num_cuota(o.get(lado))
        return None

    # 1X2 / Ganador
    if any(k in texto for k in ("1x2", "ganador", "moneyline", " ml", "cualquier equipo gana")):
        m = _mercado("ml", "moneyline", "match winner", "1x2")
        for o in (m or {}).get("odds") or []:
            if not isinstance(o, dict):
                continue
            lado = _lado()
            if lado and _num_cuota(o.get(lado)):
                return _num_cuota(o.get(lado))
            if "empate" in texto and _num_cuota(o.get("draw")):
                return _num_cuota(o.get("draw"))
        return None

    # Doble oportunidad (1X / 12 / X2)
    if "doble oportunidad" in texto or "doble" in texto:
        m = _mercado("double chance")
        sel = lado_txt.replace(" ", "")
        clave = next((k for k in ("1x", "12", "x2") if k in sel), None)
        for o in (m or {}).get("odds") or []:
            if isinstance(o, dict) and clave:
                val = _num_cuota(o.get(clave.upper()) or o.get(clave))
                if val:
                    return val
        return None

    # Handicap (Spread)
    if "handicap" in texto or "spread" in texto:
        m = _mercado("spread", "handicap")
        linea = None
        mm = re.search(r"([+-]?\d+(?:\.\d+)?)", str(selection))
        if mm:
            try:
                linea = float(mm.group(1))
            except ValueError:
                linea = None
        lado = _lado()
        # El hdp de odds-api se expresa sobre el LOCAL: para el visitante la
        # linea se invierte (Real Betis -1.5 == Getafe +1.5)
        esperado = None
        if linea is not None and lado:
            esperado = linea if lado == "home" else -linea
        if lado:
            for tolerancia in (0.01, 0.26):
                for o in (m or {}).get("odds") or []:
                    if not isinstance(o, dict):
                        continue
                    try:
                        hdp = float(o.get("hdp"))
                    except (TypeError, ValueError):
                        continue
                    if esperado is not None and abs(hdp - esperado) > tolerancia:
                        continue
                    val = _num_cuota(o.get(lado))
                    if val:
                        return val
        return None

    # Over/Under (goles, carreras, puntos, corners, tarjetas)
    if any(k in texto for k in ("over", "under", "total", "mas de", "menos de")):
        es_corner = "corner" in texto
        es_tarjeta = "tarjeta" in texto or "card" in texto
        es_over = any(k in texto for k in ("over", "mas de"))
        linea = None
        mm = re.search(r"(\d+(?:\.\d+)?)", str(selection))
        if mm:
            try:
                linea = float(mm.group(1))
            except ValueError:
                linea = None
        for m in mercados:
            n = (m.get("name") or "").lower()
            if not any(k in n for k in ("over/under", "totals", "total")):
                continue
            if es_corner and "corner" not in n:
                continue
            if es_tarjeta and "card" not in n:
                continue
            if not es_corner and not es_tarjeta and ("corner" in n or "card" in n):
                continue
            for o in (m.get("odds") or []):
                if not isinstance(o, dict):
                    continue
                try:
                    hdp = float(o.get("hdp"))
                except (TypeError, ValueError):
                    hdp = None
                if linea is not None and hdp is not None and abs(hdp - linea) > 0.01:
                    continue
                val = _num_cuota(o.get("over") if es_over else o.get("under"))
                if val:
                    return val
        return None

    # Ambos equipos marcan (BTTS)
    if "ambos" in texto or "btts" in texto:
        m = _mercado("both teams", "btts")
        sel = (selection or "").strip().lower()
        es_si = sel in ("si", "sí", "yes") or "si" in lado_txt.split()
        for o in (m or {}).get("odds") or []:
            if isinstance(o, dict):
                val = _num_cuota(o.get("yes") if es_si else o.get("no"))
                if val:
                    return val
        return None

    return None

# ============================================================
# MERCADOS DEFINIDOS POR DEPORTE (unico catalogo permitido)
# ============================================================

# Mercados PROHIBIDOS: "sin empate" (Draw No Bet) se retiro del catalogo
# porque confundia ("Liverpool empate no apuesta (DNB)"). Si un modelo lo
# devuelve igual, el pick se descarta: nunca se muestra en el dashboard.
MERCADOS_PROHIBIDOS = (
    "sin empate", "empate no", "no apuesta", "draw no bet", "dnb",
)


def _mercado_prohibido(*textos: str) -> bool:
    """True si algun texto menciona un mercado prohibido."""
    for t in textos:
        bajo = (t or "").lower()
        if any(frase in bajo for frase in MERCADOS_PROHIBIDOS):
            return True
    return False


MERCADOS_FUTBOL = [
    "1X2 (equipo A o B)",
    "Over de goles (minimo 1.25 segun la cuota)",
    "Doble oportunidad (1X, X2, 12)",
    "Total tiros de esquina, minimo 7.5 corners, cuota minima 1.25",
    "Total tiros de esquina de equipo A o B, minimo 3.5",
    "Primera mitad tiros de esquina, minimo 3.5",
    "Ambos equipos +4 tiros de esquina cada uno (SI/NO)",
    "Ambos equipos +2 tiros de esquina cada uno (SI/NO)",
    "Ambos equipos +1 tarjeta cada uno (SI/NO)",
    "Ambos equipos +2 tarjetas cada uno (SI/NO)",
    "Ambos equipos marcan",
    "Handicap europeo o asiatico equipo A o B (min +3.5 / max -2.5)",
    "Equipo A total de goles over 0.5",
    "Equipo B total de goles over 0.5",
    "Multigoles",
    "Equipo A gana cualquier mitad (SI/NO)",
    "Equipo B gana cualquier mitad (SI/NO)",
    "Cualquier equipo gana",
    "Ambos equipos marcan o 2.5 goles",
    "Total fueras de juego equipo A o B, minimo 2.5",
    "Tiros a puerta del jugador, minimo 0.5 o 1.5",
    "Tiros en general del jugador",
    "Jugador que marca o asiste",
    "Over de tarjetas",
    "Goleador en cualquier momento",
    "Equipo A o B gana la primera mitad",
     "Props de jugadores",
    "Under/Over de tiros generales equipo A o B",
    "Under/Over de tiros a puerta equipo A o B",
    "Total de faltas equipo A o B",
    "Under/Over de tarjetas equipo A o B",
]

MERCADOS_NBA = [
    "Ganador (incl. prorroga)",
    "Handicap (min positivo +25.5 / max negativo -1)",
    "Total de puntos (incl. prorroga) - USAR SIEMPRE EL UNDER MAS BAJO DEL PARTIDO COMO PRIORIDAD (ej: si el under mas bajo es 210.5, usar ese, NO 220.5)",
    "Total de puntos del equipo A",
    "Total de puntos del equipo B",
    "Minimo de puntos del jugador",
    "Minimo de rebotes del jugador",
    "Minimo de asistencias del jugador",
    "Minimo de triples anotados del jugador",
    "Minimo puntos+rebotes del jugador",
    "Minimo puntos+asistencias del jugador",
    "Minimo asistencias+rebotes del jugador",
    "Minimo puntos+rebotes+asistencias del jugador",
    "Jugador hace doble-doble (SI/NO)",
    "Jugador hace triple-doble (SI/NO)",
    "Ambos equipos anotaran 100 puntos (SI/NO)",
    "Ambos equipos anotaran 110 puntos (SI/NO)",
    "Ambos equipos anotaran OVER 100 puntos Y equipo A o B gana (SI/NO)",
    "Ambos equipos anotaran OVER 110 puntos Y equipo A o B gana (SI/NO)",
    "Ambos equipos anotaran UNDER 110 puntos Y equipo A o B gana (SI/NO)",
    "Total asistencias del equipo A o B",
    "Total robos del equipo A o B",
    "Total triples del equipo A o B",
    "Total rebotes del equipo A o B",
    "1er cuarto total de puntos",
    "Equipo A o B gana la primera mitad",
    "Carrera a 10 puntos equipo A o B",
    "Carrera a 20 puntos equipo A o B",
    "Primera mitad - total de puntos",
    "Primera mitad - equipo A o B total de puntos",
    "Primer cuarto total de puntos",
    "Primer cuarto - handicap",
]

MERCADOS_MLB = [
    "Ganador incl extra innings",
    "Totales incl extra innings",
    "Handicap incl extra innings (positivo o negativo)",
    "Ganador y total incl extra innings",
    "Hits mas de/menos de incl extra innings",
    "Equipo A hits mas de/menos de incl extra innings",
    "Equipo B hits mas de/menos de incl extra innings",
    "Bases totales por jugador incl extra innings",
    "Hits totales del jugador incl extra innings",
    "HR totales del jugador incl extra innings",
    "Strikeouts (SO) del jugador incl extra innings",
    "Equipo A totales de runs over/under",
    "Equipo B totales de runs over/under",
    "Hits + carreras + RBIs del jugador incl extra innings",
    "Lanzador total hits permitidos incl extra innings",
]

MERCADOS_NFL = [
    "Ganador (incl. prorrroga overtime)",
    "Handicap (spread)",
    "Total de puntos del partido",
    "Total de puntos del equipo A",
    "Total de puntos del equipo B",
    "Puntos del pase del jugador (over/under)",
    "-yard del receptor (receptions + receiving yards)",
    "Total de receptiones del jugador",
    "Rushing yards del jugador",
    "Sacks del jugador",
    "Intercepciones del jugador",
    "Field goals del kicker",
    "Total de touchdowns del jugador (cualquier tipo)",
    "Minimo de puntos + recepciones del jugador",
]

MERCADOS_TENIS = [
    "Ganador",
    "Juegos",
    "Sets",
    "Handicap",
    "Primer set ganador",
    "Segundo set ganador",
    "Handicap de sets",
    "Handicap de juegos",
    "Total juegos (priorizar SIEMPRE el under mas bajo del mercado)",
    "Marcador exacto",
    "Jugador A total juegos",
    "Jugador B total juegos",
    "Gana un set jugador A",
    "Gana un set jugador B",
    "Ambos jugadores ganan un set",
    "Doble resultado (1er set/partido)",
    "Sets exactos",
    "Hitos de aces totales",
    "Aces totales jugador A",
    "Aces totales jugador B",
    "Breaks totales",
    "Jugador A total de breaks",
    "Jugador B total de breaks",
    "Hitos de doble faltas",
    "Hitos de doble faltas jugador A",
    "Hitos de doble faltas jugador B",
    "Primer set handicap de juegos",
    "Primer set total juegos under/over",
    "Segundo set total juegos under/over",
    "Encuentro total tie breaks",
]

MERCADOS_POR_DEPORTE = {
    "soccer": ("Futbol", MERCADOS_FUTBOL),
    "nba": ("NBA", MERCADOS_NBA),
    "mlb": ("MLB", MERCADOS_MLB),
    "tennis": ("Tenis", MERCADOS_TENIS),
    # NFL: el bookmaker ya lo publica (DEPORTES_EN_BOOKMAERS) pero no estaba
    # activado. Sus props de jugador se resuelven con el boxscore de ESPN,
    # igual que MLB con statsapi.mlb.com.
    "nfl": ("NFL", MERCADOS_NFL),
}

DEPORTES_DASHBOARD = list(MERCADOS_POR_DEPORTE.keys())
# Deportes que el BOOKMAKER cubre con cuotas reales pero que aun no estan en
# el dashboard. Se listan para que quede constancia de que la fuente los tiene
# (nfl/hockey) y activarlos es solo incorporarlos a MERCADOS_POR_DEPORTE.
DEPORTES_EN_BOOKMAKER = ("soccer", "nba", "mlb", "nfl", "tennis", "hockey")


def catalogo_mercados() -> dict:
    """Catalogo UNICO de mercados, el mismo para el backend y el frontend.

    Es la fuente de verdad de que mercados se pueden proponer. Antes cada
    superficie tenia su propia lista y se desincronizaban: el frontend
    tenia el catalogo completo (con mercados que el resolver no sabe
    comprobar) y el backend el recortado. Ahora ambos leen de aqui, asi que
    un mercado que se puede proponer es el mismo en los dos lados y coincide
    con lo que el sistema sabe resolver.

    Devuelve, por deporte:
        {"label": <nombre>, "deportes": <clave>, "mercados": [
            {"mercado": <nombre>, "peso": <0-100>, "resoluble": bool,
             "efectividad": <% medida o None>}, ...]}
    """
    salida = {}
    for sport, (label, mercados) in MERCADOS_POR_DEPORTE.items():
        items = []
        for m in _priorizar_mercados(mercados):
            peso = peso_mercado(m)
            if peso <= 0:
                continue  # el sistema no sabe resolverlo: no se ofrece
            items.append({
                "mercado": m,
                "peso": peso,
                "resoluble": True,
                "efectividad": _EFECTIVIDAD_MERCADO.get(_norm_mercado(m).split()[0], None),
            })
        salida[sport] = {"label": label, "deporte": sport, "mercados": items}
    return salida


# Efectividad medida en produccion (30 dias, solo picks RESUELTOS). Se usa para
# mostrar la chance real al usuario y para ordenar el catalogo; None cuando el
# mercado aun no tiene historial suficiente.
_EFECTIVIDAD_MERCADO = {
    "ganador": 80, "doble": 84, "1x2": 76, "handicap": 78,
    "totales": 72, "ambos": 41, "multigoles": 75, "over": 77,
    "hits": 66, "strikeouts": 75, "mitad": 40,
}

# ============================================================
# MERCADOS RESOLUBLES Y RANKING POR EFECTIVIDAD
# ============================================================
# El problema: se generaban mas de 500 picks y 311 acababan ANULADOS. No
# porque fueran malos, sino porque el RESOLVER no sabe decidir muchos de
# ellos. Medido sobre 30 dias, la tasa de anulacion por mercado es:
#   100% -> Apuesta sin empate, Handicap de juegos/sets, Aces, Sets exactos,
#           Doble oportunidad, Jugador A/B total juegos (TENIS: sin fuente)
#    92% -> 'Equipo A o B gana la primera mitad'
#    88% -> 'Ganador' (tenis: el set score no cabe en el marcador de equipos)
# Un pick que no se puede resolver no cuenta como ACIERTO ni como FALLO: es
# ruido, y ademas desplaza del panel los que si son reales. Por eso el
# generador ahora pondera los mercados que el sistema SI sabe resolver.

# (a) Mercados que NO dependen de una fuente de estadisticas externa: se
#     resuelven solo con el marcador final (marcador de los equipos). Son los
#     unicos que se resuelven el 100% de las veces y sin gastar una sola
#     llamada de IA.
_MARC_RESOLUBLE_MARCADOR = (
    "ganador", "moneyline", "1x2", "ambos equipos marcan", "btts",
    "doble oportunidad", "multigol", "total de goles",
    "totales", "over de goles", "handicap", "total de puntos",
    "total del partido", "handicap de sets", "primer gol",
    # Mercados de MITAD: se resuelven con el marcador por periodos de ESPN
    # (_resolver_por_mitades). Antes estaban en la lista de irresolubles y por
    # eso 'Nacional gana cualquier mitad' se rechazaba pese a que el bookmaker
    # lo publica a 1.27.
    "cualquier mitad", "ambas mitades", "1x2 1", "1ª mitad", "1a mitad",
)
# (b) Mercados que el resolver cubre con una fuente de estadisticas real
#     (boxscore de MLB / stats de Sofascore). Resolubles, pero dependen de que
#     esa fuente responda.
_MARC_RESOLUBLE_STATS = (
    "hits", "strikeout", "hr totales", "jonrones",
    "total de hits", "rebotes", "asistencias",
    "tarjeta", "corner", "esquina",
)
# (c) Mercados que hoy NO se sabe resolver: props complejos de jugador, sets
#     exactos de tenis, handicaps de juegos, hitos, marcador exacto. Se
#     excluyen del catalogo efectivo: no es que sean malos, es que su
#     resultado no se puede comprobar y ends ANULADO.
_MARC_NO_RESOLUBLE = (
    "apuesta sin empate", "sin empate", "dnb",
    "sets exactos", "marcador exacto", "sets exactos",
    "handicap de juegos", "handicap de sets", "juegos",
    "aces", "dobles faltas", "break", "tie break",
    "primer set", "segundo set", "primer cuarto", "1er cuarto", "primer medio",
    "carrera a", "meta",
    "minimo puntos", "minimo rebotes", "minimo asistencias", "minimo triples",
    "puntos+rebotes", "puntos+asistencias", "asistencias+rebotes", "doble-doble",
    "triple-doble", "lanzador total", "bases totales",
)


def _norm_mercado(market: str) -> str:
    """Normaliza un nombre de mercado para poder compararlo.

    Tres diferencias hacian que un mercado REAL del bookmaker se rechazara
    como si no existiera, y el partido se perdia en vez de reanalizarse:
      * parentesis: 'Hándicap (incl. extra innings)' vs 'Handicap incl...'
      * acentos: el bookmaker escribe 'Hándicap' y el catalogo 'Handicap'
      * mayusculas: 'Doble oportunidad' vs 'doble oportunidad'
    """
    t = (market or "").lower()
    t = t.replace("(", " ").replace(")", " ")
    # Transliterar acentos: 'Hándicap' -> 'Handicap'. Se usa NFD para separar
    # el acento y se descarta SOLO el caracter acentuado: borrarlo entero
    # ('hándicap' -> 'hndicap') hacia que el mercado real no coincidiera con el
    # catalogo y el partido se perdiera.
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))
    t = "".join(c for c in t if c.isascii() or c.isspace())
    t = " ".join(t.split())
    return t


def mercado_resoluble(market: str) -> bool:
    """True si el sistema sabe resolver este mercado por su cuenta.

    Si es False, el pick solo se resuelve con la IA y 6 verificaciones
    multi-fuente: en la practica acababa ANULADO. Se usa para priorizar y para
    reanalizar con otro mercado.
    """
    t = _norm_mercado(market)
    if not t:
        return False
    if any(x in t for x in _MARC_NO_RESOLUBLE):
        return False
    # Mercados POR PARTES del partido (inning, cuarto, tiempo). Contienen
    # 'ganador' o '1x2' asi que pasaban el filtro general, pero el resolver
    # solo tiene el marcador FINAL: no puede saber quien va ganando tras el
    # inning 5. Sin este chequeo el sistema publicaba 'Innings 1 a 5 - Ganador'
    # con peso 88 y luego lo resolvia comparando el resultado del partido
    # entero, que no es lo mismo.
    # OJO: 'extra innings' NO es un mercado por partes (es el formato de MLB)
    # y contiene 'inning', asi que se excluye antes de comprobarlo.
    if "extra inning" not in t and any(x in t for x in (
        "inning", "inngs", "primer cuarto", "1er cuarto",
        "primer inning", "cuarto cuarto", "segundo cuarto", "tercer cuarto",
    )):
        return False
    return any(x in t for x in _MARC_RESOLUBLE_MARCADOR + _MARC_RESOLUBLE_STATS)


def peso_mercado(market: str) -> int:
    """Prioridad de un mercado para el generador (mayor = mejor).

    Se apoya en la efectividad REAL medida en produccion (30 dias):
      * Ganador y total        100% (5/5)
      * Doble oportunidad       84% (11/13)
      * Ganador incl. extra     80% (8/10)
      * Handicap incl. extra    80% (4/5)
      * Over de goles           77% (7/9)
      * 1X2                     76% (10/13)
      * BTTS                    41% (10/24)  <- el que mas se repite y peor
    Cuanto mas se repite un mercado, mas pesa su efectividad historica.
    """
    t = _norm_mercado(market)
    if not mercado_resoluble(t):
        return 0
    # ---- MERCADOS DE HITS: la diferencia entre equipo y jugador es enorme ----
    # Medido en produccion (45 dias):
    #   'Hits mas de/menos de' (total del PARTIDO)  1A/7F  = 12%  <- peor
    #   'Equipo A/B hits mas de/menos de'           4A/6F  = 40%
    #   'Hits totales DEL JUGADOR'                   8A/1F  = 89%  <- el bueno
    # El total de hits del partido no lo controla nadie: depende de los
    # lanzadores rivales y por eso es una moneda al aire. Los hits del
    # jugador si dependen de su propio ritmo de bateo.
    if "hit" in t:
        es_jugador = "jugador" in t
        total_partido = "partido" in t or "hits mas de/menos de" in t
        if es_jugador:
            return 86 if "total" in t else 74   # hits del jugador: 89%
        if total_partido:
            return 25                          # 12% historico: no se ofrece
        return 35                              # 40% (Equipo A/B hits)
    if "btts" in t or ("ambos equipos marcan" in t and "2.5" not in t):
        return 30          # 41% historico: el mas flojo de los buenos
    if any(x in t for x in ("1x2", "moneyline")):
        return 85          # 76%
    if "ganador" in t and "total" in t:
        return 95          # 100% (5/5) y el de menor riesgo
    if "doble oportunidad" in t or "doble oport" in t:
        return 90          # 84%
    if "ganador" in t:
        return 88          # 80%
    if "handicap" in t:
        return 78          # ~80% en MLB
    if "jugador" in t or "del jugador" in t:
        # Props de jugador: solo son resolubles los de MLB con boxscore
        # (hits/ponches/HR), no los de NBA/tenis. Se les da peso bajo para
        # que queden al final de la lista y la IA los evite por defecto.
        return 62
    if any(x in t for x in ("total de goles", "totales", "over de goles", "multigol")):
        return 70          # 72-77%
    if "cualquier mitad" in t or "ambas mitades" in t or "mitad" in t:
        # Medido: 6 ACIERTO / 9 FALLO / 6 ANULADO (40% de acierto). Ahora es
        # resoluble con el marcador por periodos, asi que el 40% es real y no
        # consecuencia de las anulaciones. Cuota baja = mas facil que gane.
        return 68
    return 60


# Palabras que identifican un pick de un deporte CONCRETO. Si un pick llega
# con una palabra que pertenece a OTRO deporte ('Aces' es de tenis, 'Rebotes'
# de NBA, 'extra innings' de MLB), es que la IA se cruzo de catalogo: se
# rechaza y se reanaliza. No deberia ocurrir (el prompt manda un solo catalogo
# por deporte) pero es una garantia barata contra un pick absurdo en el panel.
_PALABRAS_POR_DEPORTE = {
    "tennis": ("aces", "tie break", "breaks", "handicap de juegos", "handicap de sets",
               "sets exactos", "primer set", "segundo set", "marcador exacto"),
    "mlb": ("extra innings", "jonrones", "hr totales", "strikeout", "ponches"),
    "nba": ("rebotes", "triple doble", "doble doble", "primer cuarto", "1er cuarto",
            "carrera a", "cuartos"),
    "soccer": ("corners", "esquina", "tarjetas", "valla invicta", "primer gol"),
}


def mercado_de_otro_deporte(market: str, sport: str) -> bool:
    """True si el mercado usa palabras de un deporte DISTINTO al del partido."""
    t = _norm_mercado(market)
    if not t:
        return False
    for otro, palabras in _PALABRAS_POR_DEPORTE.items():
        if otro == sport:
            continue
        if any(p in t for p in palabras):
            return True
    return False


# Cuantas veces se le vuelve a preguntar a la IA cuando el pick propuesto no
# es publicable (mercado no resoluble, cuota fuera de rango, mercado que el
# sportsbook no ofrece). Cada reintento le manda la lista REAL de mercados con
# su cuota, para que elija bien a la primera. Antes un solo rechazo equivalia
# a perder el partido entero.
REINTENTOS_REANALISIS = int(os.getenv("DASHBOARD_REINTENTOS_REANALISIS", "3"))

# PROBABILIDAD MINIMA exigida por el usuario: el % de acierto medido en los
# ultimos N partidos tiene que ser 70 o mas. Por debajo se RE-ANALIZA.
PROBABILIDAD_MINIMA = int(os.getenv("DASHBOARD_PROB_MINIMA", "70"))
ULTIMOS_PARTIDOS = int(os.getenv("DASHBOARD_ULTIMOS_PARTIDOS", "10"))

try:
    from probabilidad import probabilidad_pick, linea_optima_por_promedio
except Exception:  # sin el modulo, el filtro queda inactivo (no rompe el panel)
    def probabilidad_pick(*_a, **_k):
        return None

    def linea_optima_por_promedio(*_a, **_k):
        return None, 0


def _motivo_rechazo_pick(pick: dict, partido: dict, disponibles: list, market: str):
    """Devuelve el motivo por el que el pick no es publicable, o None si lo es.

    Centraliza los filtros para poder REINTENTAR con la misma regla en vez de
    descartar en el primer fallo.
    """
    market = (market or pick.get("market") or "").strip()
    titulo = str(pick.get("titulo") or "")
    selection = str(pick.get("selection") or "")

    if _mercado_prohibido(market, titulo, selection):
        return "prohibido"

    # El mercado no puede pertenecer a otro deporte: un 'Aces' en un partido
    # de fútbol o un 'rebotes' en MLB es un pick absurdo (la IA se cruzó de
    # catálogo). Se rechaza y se reanaliza.
    if mercado_de_otro_deporte(market, partido.get("sport") or ""):
        return "otro_deporte"

    # El mercado debe existir de verdad: en el catalogo del deporte o en lo que
    # el sportsbook publico para ese evento.
    if market and _market_norm(market) not in {_market_norm(m) for m in disponibles}:
        from cuotas_doradobet import (
            detalle_mercado_para_ia,
            event_id_doradobet,
        )

        evento = event_id_doradobet(partido["sport"], partido["home_name"], partido["away_name"])
        detalle = detalle_mercado_para_ia(evento) if evento else ""
        if not detalle or _market_norm(market) not in _market_norm(detalle):
            return "no_publicado"

    # Mercados que el resolver no sabe comprobar. Antes cada uno era un pick
    # que acababa ANULADO; ahora se reintenta con otros.
    if not mercado_resoluble(market):
        return "no_resoluble"

    try:
        odds = float(pick.get("odds") or 0)
    except (TypeError, ValueError):
        odds = 0
    if not odds or not (ODDS_MINIMA <= odds <= ODDS_MAXIMA):
        return "cuota"

    rationale_txt = f"{pick.get('rationale') or ''} {titulo}"
    if (
        not _nombre_valido(partido["home_name"])
        or not _nombre_valido(partido["away_name"])
        or _texto_sin_datos(rationale_txt)
        or _texto_sin_datos(partido["event_name"])
    ):
        return "calidad"

    # FILTRO DE PROBABILIDAD REAL. El usuario exige 70% o mas medido sobre los
    # ultimos 10 partidos; si no llega, se RE-ANALIZA el partido buscando otro.
    # Solo bloquea si la probabilidad se pudo MEDIR: cuando no hay datos no se
    # inventa un porcentaje, y el filtro de resolubilidad sigue siendo el que
    # protege. (Ej: 'Schwarber 1+ hit' daba 30% real, asi que este filtro es
    # el que de verdad evita los picks malos, no la categoria del mercado.)
    prob = probabilidad_pick(
        pick, partido.get("sport") or "", partido.get("league"), ULTIMOS_PARTIDOS
    )
    if prob is not None and prob < PROBABILIDAD_MINIMA:
        return "baja_probabilidad"

    return None


def _seleccionar_mercados_ia(mercados: list, limite: int = 20) -> list:
    """Elige que mercados reales se le ofrecen a la IA en el reintento.

    El bookmaker publica cientos (146 en un partido de fútbol). Mandarlos
    todos seria gastar el prompt en repetir el catalogo y la IA se perderia.
    Se priorizan los que el sistema sabe RESOLVER (peso alto) y, a igual, los
    de mayor probabilidad (cuota mas baja), que son los que el usuario quiere.
    """
    def _clave(m):
        return (-peso_mercado(m.get("market") or ""), float(m.get("odds") or 9.9))
    return sorted(mercados, key=_clave)[:limite]


def _mensaje_reanalisis(partido: dict, label: str, mercados_reales: list,
                        motivo: str, intento: int) -> str:
    """Mensaje para el reintento: la IA elige entre mercados REALES y su cuota.

    'mercados_reales' viene del sportsbook con el precio ya filtrado al rango
    publicable, asi que cualquier opcion que se le ofrezca es apostable de
    verdad. Es la diferencia entre una cuota estimada (que luego resultaba
    imposible de jugar) y la cuota real.
    """
    explicacion = {
        "no_resoluble": (
            "ese mercado no se puede verificar despues (no hay forma de saber si "
            "acierto o fallo al final del partido), asi que no sirve"
        ),
        "cuota": f"su cuota no estaba entre {ODDS_MINIMA} y {ODDS_MAXIMA}",
        "no_publicado": "ese mercado no lo publica el bookmaker para este partido",
        "prohibido": "ese tipo de apuesta esta prohibido en la plataforma",
        "baja_probabilidad": (
            f"su probabilidad real en los ultimos {ULTIMOS_PARTIDOS} partidos es "
            f"INFERIOR al {PROBABILIDAD_MINIMA}% exigido, asi que no se publica. "
            f"Elige otro mercado del catalogo que SI llegue al "
            f"{PROBABILIDAD_MINIMA}% segun su historial real"
        ),
        "otro_deporte": (
            "ese mercado no existe en este deporte (parece de otro: en futbol "
            "no hay aces, en baseball no hay rebotes, en tenis no hay corners)"
        ),
        "calidad": "el pick tenia datos genericos o incompletos",
    }.get(motivo, "el pick no era publicable")

    base = (
        f"Partido: {partido['away_name']} (visitante) vs "
        f"{partido['home_name']} (local)\n"
        f"Deporte: {label}\n"
        f"Fecha/hora: {partido['date']}\n\n"
        f"Tu propuesta anterior NO sirve porque {explicacion}.\n\n"
        "Elige OTRA apuesta, obligatoriamente de esta lista de mercados REALES "
        "que el bookmaker tiene publicados AHORA para este partido, con su "
        "cuota exacta (no inventes ni ajustes la cuota):\n"
    )
    if mercados_reales:
        elegidos = _seleccionar_mercados_ia(mercados_reales)
        base += "\n".join(
            f"- [{m['odds']}] {m['titulo']}   (market: '{m['market']}', "
            f"seleccion: '{m['selection']}')"
            for m in elegidos
        )
        base += (
            f"\n({len(elegidos)} de {len(mercados_reales)} mercados publicables; "
            "estan ordenados de mas a menos seguro)"
        )
    else:
        base += (
            "(el bookmaker no publica mercados con cuota en rango para este "
            "partido: elige el mercado del catalogo mas seguro y estima la "
            f"cuota entre {ODDS_MINIMA} y {ODDS_MAXIMA})"
        )
    base += (
        f"\n\nIntento {intento} de {REINTENTOS_REANALISIS}. Prefiere el mercado con "
        "mas probabilidad de acierto dentro de los disponibles. Responde SOLO "
        "con el JSON del pick, sin texto extra."
    )
    # Si hay props de jugador entre las opciones, se manda tambien su forma
    # reciente: sin esto la IA elige entre jugadores sin saber como vienen.
    base += _form_jugadores_props(mercados_reales)
    return base


def _mlb_id_por_nombre(nombre: str):
    """MLB playerId a partir del nombre ('Kyle Schwarber' -> 656941).

    El bookmaker publica los props por NOMBRE, pero statsapi.mlb.com (la unica
    fuente de forma reciente por jugador) indexa por id. Sin esta busqueda no
    habia forma de darle a la IA los ultimos partidos de Schwarber.
    """
    import requests

    try:
        r = requests.get(
            f"{MLB_BASE}/people/search", params={"names": nombre}, timeout=12
        )
        people = r.json().get("people") or []
    except Exception:
        return None
    if not people:
        return None
    # Se elige el que coincide por apellido: 'Schwarber' puede devolver varios.
    apellido = _norm_texto(nombre).split()[-1] if _norm_texto(nombre) else ""
    for p in people:
        if apellido and apellido in _norm_texto(p.get("fullName") or ""):
            return p.get("id")
    return people[0].get("id")


def _mlb_forma_jugador(nombre: str, ultimos: int = 5) -> str:
    """Forma reciente de un jugador de MLB en una linea legible.

    Devuelve algo como:
        'Kyle Schwarber: ultimos 5 -> 2.0 hits (4 de 5 con al menos 1), '
        '0.4 HR, media .245'

    Antes el generador solo daba forma de EQUIPO, asi que publicaba props de
    jugador ('Schwarber 1+ hit @ 1.59') sin darle a la IA un solo dato del
    jugador: la IA apostaba a ciegas en el jugador.
    """
    pid = _mlb_id_por_nombre(nombre)
    if not pid:
        return ""
    import requests

    try:
        r = requests.get(
            f"{MLB_BASE}/people/{pid}/stats",
            params={"stats": "gameLog", "group": "hitting", "season": datetime.now().year},
            timeout=15,
        )
        splits = ((r.json().get("stats") or [{}])[0].get("splits") or [])
    except Exception:
        return ""
    if not splits:
        return ""
    ult = splits[-ultimos:]
    hits, hrs, runs, ab, con_hit = [], [], [], 0, 0
    for s in ult:
        st = s.get("stat") or {}
        def _n(k):
            try:
                return int(st.get(k) or 0)
            except (TypeError, ValueError):
                return 0
        h = _n("hits")
        hits.append(h)
        hrs.append(_n("homeRuns"))
        runs.append(_n("runs"))
        ab += _n("atBats")
        if h > 0:
            con_hit += 1
    if not hits:
        return ""
    prom_h = sum(hits) / len(hits)
    return (
        f"{nombre}: ultimos {len(hits)} -> {prom_h:.1f} hits de media "
        f"({con_hit} de {len(hits)} con al menos 1), {sum(hrs)} HR, "
        f"{sum(runs)} carreras, {ab} turnos al bate"
    )


def _form_jugadores_props(mercados_reales: list, limite: int = 4) -> str:
    """Texto con la forma reciente de los jugadores con props publicados.

    Solo se consulta a los que de verdad tienen mercados de jugador en este
    partido y con cuota en rango: son los unicos que la IA podria elegir, y
    cada consulta cuesta una llamada a statsapi.
    """
    if not mercados_reales:
        return ""
    jugadores = []
    vistos = set()
    for m in mercados_reales:
        j = m.get("jugador")
        if not j or j in vistos:
            continue
        vistos.add(j)
        jugadores.append(j)
        if len(jugadores) >= limite:
            break
    lineas = []
    for j in jugadores:
        try:
            t = _mlb_forma_jugador(j)
        except Exception:
            t = ""
        if t:
            lineas.append("- " + t)
    if not lineas:
        return ""
    return (
        "\n\nFORMULA RECIENTE DE LOS JUGADORES CON MERCADOS PUBLICADOS "
        "(statsapi.mlb.com, datos reales):\n" + "\n".join(lineas)
        + "\nSi eliges un prop de jugador, basate en estos numeros reales, "
        "no en lo que recuerdes de su nombre."
    )


def _pick_deterministico(partido: dict, mercados_reales: list, label: str):
    """Elige el mejor pick SIN llamar a la IA.

    Por que existe: la cuota de Groq es por dia y, cuando se agota, el ciclo
    terminaba con 0 picks aunque el bookmaker tuviera mercados con cuota
    real. Pero esos mercados ya traen la informacion que hace falta:
      * la cuota REAL (menor cuota = mayor probabilidad de acierto) y
      * la efectividad medida de cada mercado en produccion (peso_mercado).

    Asi que el pick se elige por el mismo criterio que usaba la IA en su
    regla 5 ('la apuesta mas FACIL de acertar con valor'), pero sin gastar un
    solo token: el mercado resoluble de mayor peso y, a igualdad, el de menor
    cuota. El resultado es defendible y, a diferencia de una estimacion de la
    IA, la cuota es la real del bookmaker.

    Devuelve None si no hay ningun mercado publicable y resoluble.
    """
    if not mercados_reales:
        return None
    candidatos = [m for m in mercados_reales if mercado_resoluble(m.get("market") or "")]
    if not candidatos:
        return None

    def _clave(m):
        return (-peso_mercado(m.get("market") or ""), float(m.get("odds") or 9.9))

    mejor = sorted(candidatos, key=_clave)[0]
    cuota = float(mejor.get("odds") or 0)
    peso = peso_mercado(mejor.get("market") or "")
    es_golden = _es_golden(cuota) and peso >= 80
    if es_golden:
        confianza = "ALTA"
    elif peso >= 85:
        confianza = "ALTA"
    elif peso >= 70:
        confianza = "MEDIA"
    else:
        confianza = "BAJA"
    return {
        "market": mejor.get("market"),
        "titulo": mejor.get("titulo"),
        "selection": mejor.get("selection"),
        "odds": cuota,
        "confidence": confianza,
        "rationale": (
            f"Elegido por criterio cuantitativo: mercado de efectividad "
            f"probada (peso {peso}/100) y la cuota mas baja entre los "
            f"resolubles de este partido, que es la que mas probabilidad de "
            f"acierto tiene. Cuota real del bookmaker, no estimada."
        ),
        "stats": [
            f"Cuota real: {cuota} (menor = mas probable)",
            f"Mercado: {mejor.get('market')}",
            f"Seleccion: {mejor.get('selection')}",
        ],
    }


def _priorizar_mercados(mercados: list) -> list:
    """Ordena el catalogo de un deporte: primero los que mas aciertan y mas
    se resuelven. La IA elige de esta lista, asi que subir aqui el peso de los
    buenos baja el de los que acaban ANULADOS o fallando."""
    return sorted(mercados, key=lambda m: (-peso_mercado(m), m))


# ============================================================
# PROMPT MAESTRO (usado por LAS DOS IAS: 365AI y Demian)
# ============================================================


def catalogo_texto(sport: str | None = None) -> str:
    """Catalogo de mercados.

    Con sport=None devuelve el catalogo completo (todos los deportes); con un
    sport concreto devuelve SOLO el de ese deporte. El generador usa la
    version por deporte: mandar el catalogo entero en cada llamada costaba
    ~1470 tokens de prompt y el TPD de Groq se agotaba antes de generar
    media docena de picks.
    """
    lineas = []
    deportes = MERCADOS_POR_DEPORTE if sport is None else {sport: MERCADOS_POR_DEPORTE[sport]}
    for s, (label, mercados) in deportes.items():
        lineas.append(f"{label.upper()}:")
        # Solo los mercados que el sistema sabe RESOLVER, y ordenados por su
        # efectividad real: la IA elige de esta lista, asi que lo que queda al
        # final es lo que ella acababa eligiendo (y anulando).
        for m in _priorizar_mercados(mercados):
            if not mercado_resoluble(m):
                continue
            linea = f"- {m}"
            # Se marca el rango de cuota de cada mercado: el golden necesita
            # 1.35-1.40 y la IA no tenia forma de saber que mercado puede darlo.
            if 35 <= peso_mercado(m) <= 70:
                linea += "  (cuotatipica 1.35-1.80)"
            lineas.append(linea)
        lineas.append("")
    return "\n".join(lineas)


# REGLAS (sin catalogo): se combinan con el catalogo del deporte concreto en
# prompt_picks_deporte(). PROMPT_PICKS se mantiene completo por compatibilidad.
REGLAS_PICKS = """
REGLAS OBLIGATORIAS:
1. USA SOLO los mercados listados arriba. NUNCA inventes mercados. analizaras con los endpoint y api que usa la api sin delatarlo ni decir que usas
2. Los textos del catalogo son INSTRUCCIONES/REGLAS del mercado (minimos de
   linea, cuota minima, limites de handicap, etc.), NO texto literal para el
   usuario. Interpretalos: eligen la linea concreta que cumpla esas reglas.
3. NUNCA repitas el mismo mercado en los diferentes o mismos partidos.
4. SIEMPRE ve variando las opciones: no te centres solo en 1X2 o goles.
5. Busca SIEMPRE la apuesta mas FACIL de acertar CON VALOR (cuota justa vs probabilidad real).
6. CUOTA OBLIGATORIA: el campo "odds" DEBE ser un numero entre 1.20 y 2.50.
   El rango ELITE 1.35-1.40 es GOLDEN PICK (lo marca el sistema, no tu).
   Si el mercado no tiene cuota en 1.20-2.50, ELIGE OTRO mercado/linea que
   si la tenga. PROHIBIDO devolver null o fuera de rango.
7. Respeta los minimos indicados (handicap minimo, under mas bajo en NBA/tenis).
8. PROHIBIDO apostar a los MERCADOS QUE NO CONTROLA NADIE. Estos tienen
   efectividad medida en produccion y son la mayoria de los fallos:
   - Total de hits/goles/puntos DEL PARTIDO      12% de acierto (1 de 8).
     Depende de los rivales, no de tu equipo. NO lo elijas NUNCA.
   - Total de un EQUIPO entero (Equipo A/B hits)  40% de acierto.
   - 'Ambos equipos marcan' (BTTS)              41% de acierto.
   En su lugar, elige mercados de UN JUGADOR (sus hits, jonrones, ponches:
   89% de acierto) o de resultado de EQUIPO (ganador 80%, doble oportunidad
   84%, handicap 78%, totales 72%).
9. No elijas un UNDER por sistema. Un 'Under 3.5' a cuota alta (1.85) es una
   apuesta de que el partido sera aburrido, y no tienes ninguna ventaja para
   pensarlo: si la cuota es alta es porque el mercado la ve improbable. Para
   una apuesta Under solo elige una linea con cuota BAJA (<=1.45), que
   significa que es lo esperable. Si te sale un Under a 1.85 o mas, es
   significa que ese lado no es lo esperable: cambia de mercado.
10. La cuota que se te da es la REAL del bookmaker. Si un mercado aparece con
   cuota alta, no lo 'corrijas' inventando una cuota baja: es que ese lado es
   improbable.
11. Responde EXCLUSIVAMENTE con un JSON valido, sin texto extra, con esta forma exacta:
{
  "market": "<nombre del mercado del catalogo que elegiste (para validar)>",
  "titulo": "<apuesta en lenguaje natural y corto para mostrar al usuario. Ejemplos: 'Corners de Club Brugge: Over 3.5', 'Total de corners del partido: Over 7.5', 'Ambos equipos marcan: SI', 'HÃ¡ndicap asiatico Real Madrid -1.5', 'Total de puntos Lakers: Under 210.5'>",
  "selection": "<seleccion concreta: equipo A/B, SI/NO, over/under X.X, etc>",
  "odds": <cuota decimal estimada o null>,
  "confidence": "<ALTA|MEDIA|BAJA>",
  "rationale": "<1-2 frases del edge en espanol>",
  "stats": ["<dato corto 1 basado en los ultimos 5 partidos>", "<dato corto 2>", "<dato corto 3>", ...]
}
"""

_cabecera_prompt = (
    "Eres 3SIXTYBETS AI, analista cuantitativo de apuestas deportivas.\n\n"
    "SOLO PUEDES USAR ESTOS PICKS DEFINIDOS PARA CADA DEPORTE:\n\n"
)

# Cache de prompts por deporte (el catalogo no cambia en caliente).
_prompt_por_deporte: dict = {}


def prompt_picks_deporte(sport: str) -> str:
    """System prompt SOLO con el catalogo del deporte indicado.

    Reduce el prompt de ~1470 a ~400 tokens: es la palanca mas grande para
    que el TPD de Groq aguante una jornada completa de picks.
    """
    if sport not in _prompt_por_deporte:
        _prompt_por_deporte[sport] = (
            _cabecera_prompt + catalogo_texto(sport) + REGLAS_PICKS
        )
    return _prompt_por_deporte[sport]


PROMPT_PICKS = _cabecera_prompt + catalogo_texto() + REGLAS_PICKS

# Prompt para VERIFICACIONES (confirmar un pick / decidir ACIERTO-FALLO).
# No necesita el catalogo de mercados: la tarea es de una sola respuesta
# corta. Usar el prompt maestro aqui multiplicaba por 3-4 el gasto de tokens
# sin aportar nada (las verificaciones son las llamadas mas numerosas).
PROMPT_VERIFICACION = (
    "Eres un verificador de apuestas de 3SIXTYBETS. Contrastas la propuesta "
    "con datos reales de fuentes externas (Sofascore, Flashscore, Fotmob, "
    "estadisticas oficiales). Eres escuetico: NO confirmas por defecto. "
    "Responde EXCLUSIVAMENTE con el formato pedido y nada mas."
)


# ============================================================
# GENERACION DE PICKS
# ============================================================


def _partidos_hoy():
    """Partidos elegibles para analisis: en vivo + los que arrancan pronto.

    Ventana: los que inician dentro de las proximas VENTANA_ANALISIS_H horas
    (y los EN VIVO siempre). A las :00 Nicaragua los partidos de las grandes
    ligas europeas arrancan de madrugada/manana: sin esta ventana no se
    analizaria ninguno. Los finalizados nunca entran.

    Orden: PRIMERO las 5 mejores ligas de Europa (en orden de prioridad y por
    hora de inicio), luego el resto de ligas y deportes.
    """
    import sports

    ahora_utc = datetime.now(timezone.utc)
    limite = ahora_utc + timedelta(hours=VENTANA_ANALISIS_H)
    partidos = []
    for sport in DEPORTES_DASHBOARD:
        try:
            data = sports.get_sport_games(sport)
            for g in data.get("games", []):
                if g.get("state") == "post":
                    continue  # ya finalizados: no generar pick nuevo
                if g.get("state") != "in":
                    # Programado: debe arrancar dentro de la ventana.
                    try:
                        inicio = datetime.fromisoformat(
                            str(g.get("date", "")).replace("Z", "+00:00")
                        )
                        if inicio.tzinfo is None:
                            inicio = inicio.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        continue
                    if not (ahora_utc <= inicio <= limite):
                        continue
                home = g.get("home") or {}
                away = g.get("away") or {}
                # Fallback tenis/MMA: 'teams' si no hay home/away
                if not home and not away:
                    equipos = g.get("teams") or []
                    away = equipos[0] if equipos else {}
                    home = equipos[1] if len(equipos) > 1 else {}
                home_name = home.get("name") or "?"
                away_name = away.get("name") or "?"
                # league_code: NECESARIO para resolver el pick mas tarde. El
                # resolver consulta el summary de ESPN por evento usando la
                # liga; sin ella solo podia buscar en el scoreboard del dia, el
                # evento desaparecia a las pocas horas y el pick acababa
                # ANULADO. Se deriva del path de ESPN (SPORTS['mlb'] =
                # 'baseball/mlb'). En futbol ya venia; en MLB/NBA/tenis no, y
                # eran 191 + 99 picks que no se podian resolver nunca.
                league = g.get("league_code")
                if not league:
                    try:
                        import sports as _sports_mod

                        _path = (_sports_mod.SPORTS.get(sport) or ("", ""))[0]
                        if _path and "/" in _path:
                            league = _path
                    except Exception:
                        league = None

                partidos.append({
                    "sport": sport,
                    "label": data.get("label", sport),
                    "event_id": str(g.get("id", "")),
                    "event_name": f"{away_name} vs {home_name}",
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_logo": home.get("logo"),
                    "away_logo": away.get("logo"),
                    "date": g.get("date", ""),
                    "state": g.get("state"),
                    "odds": g.get("odds"),
                    "league": league,
                })
        except Exception as exc:
            print(f"[Dashboard] Error trayendo partidos {sport}: {exc}")

    # PRIORIDAD: primero las 5 mejores ligas de Europa (en el orden de
    # LIGAS_TOP_EUROPA y por hora de arranque), despues el resto (en vivo
    # primero, luego por hora). Con max_partidos=80 y jornadas cargadas, sin
    # este orden las grandes ligas podian quedar fuera del recorte.
    def _clave(p):
        liga = (p.get("league") or "").lower()
        if liga in LIGAS_TOP_EUROPA:
            return (0, LIGAS_TOP_EUROPA.index(liga), p.get("date") or "")
        return (1, 0 if p.get("state") == "in" else 1, p.get("date") or "")

    partidos.sort(key=_clave)

    grandes = sum(
        1 for p in partidos if (p.get("league") or "").lower() in LIGAS_TOP_EUROPA
    )
    if partidos:
        print(
            f"[Dashboard] Partidos en ventana: {len(partidos)} "
            f"(5 grandes ligas: {grandes})",
            flush=True,
        )
    return partidos


def _es_error_ia(texto) -> bool:
    """True si el 'texto' es en realidad un mensaje de error del proveedor.

    Delega en ai.proveedor_fallos, que es la lista unica de marcas. Antes esta
    logica estaba copiada aqui y en main.py, y cada copia se quedaba vieja: al
    expirar el plan de prueba de You.com su texto paso a 'Your platform trial
    has ended' y NINGUNA copia lo reconocia. El mensaje se colaba como si fuera
    la respuesta del modelo y el partido se descartaba como 'sin datos' (6 h de
    bloqueo) cuando en realidad no habia cuota.
    """
    from ai.proveedor_fallos import respuesta_nula

    return respuesta_nula(texto)



def _preguntar_ia(mensaje: str, sport: str | None = None, system_prompt: str | None = None, buscar_web: bool = True):
    """Genera los picks del Dashboard con su motor DEDICADO (ai/dashboard_ia.py).

    Antes usaba Demian (You.com) directamente. Ahora el dashboard tiene modelo
    propio: si el chat satura un modelo, el dashboard sigue respondiendo, porque
    Groq aplica los limites POR MODELO. You.com queda solo como ultimo recurso
    cuando ya no queda saldo.

    'sport' recorta el catalogo del system prompt al de ese deporte: mandar los
    4catalogos en cada llamada costaba ~1470 tokens de prompt y quemaba el TPD.
    'system_prompt' permite pasar un prompt propio (verificaciones), que es
    mucho mas barato que el prompt maestro completo.
    """
    if system_prompt is None:
        system_prompt = (
            prompt_picks_deporte(sport)
            if sport in MERCADOS_POR_DEPORTE
            else PROMPT_PICKS
        )
    # 1) Motor dedicado del dashboard.
    try:
        from ai.dashboard_ia import generar_picks

        contenido, modelo = generar_picks(system_prompt, mensaje, buscar_web=buscar_web)
        if contenido and not _es_error_ia(contenido):
            return contenido, f"365AI Dashboard ({modelo})"
    except Exception as exc:
        print(f"[Dashboard] motor dedicado fallo: {exc}", flush=True)

    # 2) Relevo: Demian (You.com), util solo si quedo saldo.
    try:
        from ai.model import generar_respuesta_you

        contenido = generar_respuesta_you(system_prompt, mensaje)
        if contenido and not _es_error_ia(contenido):
            return contenido, "Demian tipster"
    except Exception as exc:
        print(f"[Dashboard] Demian fallo: {exc}")

    # 'SIN_CUOTA' distingue "no hubo respuesta por limite de cuota" de un fallo
    # puntual: el generador usa esa marca para NO descartar el partido.
    return None, "SIN_CUOTA"


def _parsear_pick_json(texto: str):
    """Extrae el JSON del pick de la respuesta de la IA (tolerante a markdown)."""
    if not texto:
        return None
    limpio = re.sub(r"```(?:json)?|```", "", texto).strip()
    match = re.search(r"\{.*\}", limpio, re.DOTALL)
    if not match:
        return None
    try:
        pick = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(pick, dict) or not pick.get("market") or not pick.get("selection"):
        return None
    return pick


def _market_norm(market: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (market or "").lower())


def _ultimos5(sport: str, event_id: str, home_name: str, away_name: str) -> dict:
    """Ultimos 5 partidos reales (ESPN) de cada equipo del evento.

    Devuelve {"away": ["G 2-1 vs X", ...], "home": [...]} con resultado
    (G ganÃ³, P perdiÃ³, E empate), marcador y rival.
    """
    import sports

    def _resultado(mi, mi_score, rival, rival_score):
        if mi_score is None or rival_score is None:
            return f"vs {rival} ({r.get('status', '')})"
        try:
            mi_s, ri_s = float(mi_score), float(rival_score)
        except (TypeError, ValueError):
            return f"vs {rival} ({r.get('status', '')})"
        if mi_s > ri_s:
            res = "G"
        elif mi_s < ri_s:
            res = "P"
        else:
            res = "E"
        return f"{res} {mi_s:g}-{ri_s:g} vs {rival}"

    salida = {"home": [], "away": []}
    try:
        detail = sports.get_game_detail(sport, event_id)
    except Exception:
        return salida

    for lado, nombre in (("home", home_name), ("away", away_name)):
        equipo = next(
            (t for t in detail.get("teams", []) if t.get("name") == nombre), None
        )
        if not equipo:
            continue
        for r in (equipo.get("recent_games") or [])[:5]:
            a = r.get("away") or {}
            h = r.get("home") or {}
            if (a.get("name") or "") == nombre:
                fila = _resultado(nombre, a.get("score"), h.get("name", "?"), h.get("score"))
            elif (h.get("name") or "") == nombre:
                fila = _resultado(nombre, h.get("score"), a.get("name", "?"), a.get("score"))
            else:
                continue
            salida[lado].append(fila)
    return salida


def _doble_verificar_pick(p: dict, market: str, selection: str, label: str) -> int:
    """Doble verificacion antes de publicar un GOLDEN PICK.

    Hace VERIFICACIONES_PUBLICAR (2) pasadas independientes de la IA contra
    fuentes externas; cada pasada debe confirmar el MISMO mercado+seleccion.
    Devuelve el nro de pasadas coincidentes (0..2). Solo con 2 se publica.
    """
    ok = 0
    for i in range(VERIFICACIONES_PUBLICAR):
        pregunta = (
            f"VERIFICACION {i + 1}/{VERIFICACIONES_PUBLICAR} (independiente).\n"
            f"Partido: {p['away_name']} (visitante) vs {p['home_name']} (local)\n"
            f"Liga: {p.get('league') or '?'} · Fecha: {p.get('date') or '?'}\n"
            f"Pick propuesto: mercado '{market}' - seleccion '{selection}' ({label}).\n"
            "Busca en la web (Sofascore/Flashscore/Fotmob/estadisticas oficiales) "
            "los ultimos 5 partidos, forma local/visita, bajas y H2H. "
            "Responde SOLO JSON: {\"market\": \"...\", \"selection\": \"...\", "
            "\"confirma\": true|false}. Confirma=true SOLO si los datos apoyan "
            "esa misma seleccion; si no, confirma=false."
        )
        try:
            texto, _modelo = _preguntar_ia(pregunta, system_prompt=PROMPT_VERIFICACION, buscar_web=False)
            data = _parsear_pick_json(texto)
            if not data:
                import json as _json
                try:
                    data = _json.loads((texto or "").strip())
                except Exception:
                    data = None
            if not data:
                continue
            m2 = _market_norm(str(data.get("market") or ""))
            s2 = str(data.get("selection") or "").strip().lower()
            confirma = bool(data.get("confirma", True))
            if confirma and m2 == _market_norm(market) and s2 == str(selection or "").strip().lower():
                ok += 1
        except Exception:
            continue
    return ok


def generar_picks_dia(max_partidos: int = 120, forzar: bool = False) -> dict:
    """Genera GOLDEN PICKS automaticos (cuota 1.35-1.40, doble verificados).

    Cubre minimo MIN_JUEGOS_MANANA (15) juegos de manana: analiza hasta
    max_partidos partidos de la ventana (hoy + manana).

    Idempotente: salta partidos que ya tienen pick guardado hoy.
    FUTBOL: se genera pick para CADA partido disponible (mas volumen), el
    resto de deportes sigue la regla de no repetir mercado en la jornada.
    Analiza a cualquier hora (incluye partidos de hoy y de manana).
    """
    partidos = _partidos_hoy()
    generados = 0
    omitidos = 0
    omitidos_descartados = 0
    errores = 0
    rechazados_cuota = 0
    rechazados_calidad = 0
    rechazados_prohibido = 0
    rechazados_sin_verificar = 0
    rechazados_no_resoluble = 0
    rechazados_baja_prob = 0

    # Partidos ya analizados y descartados hace poco (cuota baja, sin datos,
    # mercado prohibido...): no se vuelven a gastar llamadas de IA en ellos.
    descartes = db.descartes_recientes(horas=6)
    try:
        db.limpiar_descartes(horas=48)
    except Exception:
        pass

    sin_cuota = False
    mercados_usados = {_market_norm(r["market"]) for r in (db.list_picks_hoy() or [])}
    con_pick = db.eventos_con_pick()

    for p in partidos[:max_partidos]:
        if p["event_id"] in con_pick:
            omitidos += 1
            continue
        if p["event_id"] in descartes:
            omitidos_descartados += 1
            continue

        label, mercados = MERCADOS_POR_DEPORTE[p["sport"]]
        disponibles = [m for m in mercados if _market_norm(m) not in mercados_usados]
        if not disponibles:
            # Todos los mercados del catalogo de este deporte ya salieron hoy.
            # Antes solo el FUTBOL podia reutilizarlos y el resto abortaba el
            # bucle, dejando el dia con 1-2 picks. Ahora TODOS los deportes
            # reutilizan: el filtro de no-repetir es una guia para la IA (se
            # le pasa la lista de usados), no un veto que vacie el dashboard.
            disponibles = list(mercados)

        mensaje = (
            f"Partido: {p['away_name']} (visitante) vs {p['home_name']} (local)\n"
            f"Deporte: {label}\nFecha/hora: {p['date']}\n"
        )

        # Cuotas reales: primero odds-api.io; si no hay, ESPN; si no, estima
        cuotas_texto = _cuotas_reales(p["sport"], p["home_name"], p["away_name"])
        if cuotas_texto:
            mensaje += cuotas_texto + "\n"
        else:
            cuotas = p.get("odds") or {}
            if cuotas.get("details") or cuotas.get("over_under"):
                mensaje += (
                    "Cuotas del sistema de mercado: "
                    f"linea={cuotas.get('details') or 'N/A'}, "
                    f"ML local={cuotas.get('home_odds') or 'N/A'}, "
                    f"ML visitante={cuotas.get('away_odds') or 'N/A'}, "
                    f"total de la casa={cuotas.get('over_under') or 'N/A'}. "
                    "BASATE en estas cuotas reales para calcular valor; la linea que "
                    "elijas debe respetar los minimos del catalogo.\n"
                )
            else:
                mensaje += (
                    "No hay cuotas reales disponibles para este partido: estima la "
                    "cuota y respetando los minimos del catalogo.\n"
                )

        # Ultimos 5 partidos reales de cada equipo (ESPN) para dar contexto
        recientes = _ultimos5(p["sport"], p["event_id"], p["home_name"], p["away_name"])
        if recientes.get("away") or recientes.get("home"):
            mensaje += (
                f"\nULTIMOS 5 PARTIDOS REALES (ESPN) de {p['away_name']}: "
                f"{'; '.join(recientes.get('away') or ['sin datos'])}\n"
                f"ULTIMOS 5 PARTIDOS REALES (ESPN) de {p['home_name']}: "
                f"{'; '.join(recientes.get('home') or ['sin datos'])}\n"
                "Analiza esas tendencias y en el campo 'stats' devuelve de 3 a 5 "
                "datos cortos (una linea cada uno) que sustenten ESTE pick, basados "
                "UNICAMENTE en esos partidos reales. Ej: 'Gano 4 de sus ultimos 5', "
                "'Marco en los ultimos 3 partidos', '2 de 3 con BTTS'.\n"
            )

        # PROMEDIOS REALES local/visitante (corners, tarjetas, faltas, tiros)
        # por partido. Antes la IA solo recibia "gano 2-1": razonaba sobre
        # intuicion y por eso los corners salian con 0 de aciertos. Con estos
        # numeros puede contrastar la linea del mercado contra la proyeccion.
        if p["sport"] == "soccer":
            try:
                from backend.apuestas import fotmob_stats as _fstats

                _ctx = _fstats.contexto_para_ia(p["home_name"], p["away_name"])
                if _ctx:
                    mensaje += _ctx
            except Exception:
                pass  # sin datos: la IA sigue con lo que tenia

        mensaje += (
            f"\nElige UN solo mercado del catalogo de {label} (que NO sea uno de estos ya "
            f"usados hoy: {', '.join(list(mercados_usados)[:15]) or 'ninguno'}). "
            f"Recuerda: el campo 'market' es para validar contra el catalogo; el campo "
            f"'titulo' es la apuesta en lenguaje natural (ej: 'Corners de "
            f"{p['home_name']}: Over 3.5'). Devuelve el JSON del pick."
            f"\nIMPORTANTE: usa los nombres REALES de los equipos/jugadores "
            f"({p['home_name']} y {p['away_name']}); PROHIBIDO picks genericos tipo "
            f"'Jugador A', 'Local' o 'equipo B'. Si de verdad no tienes datos del "
            f"partido, responde {{\"error\": \"sin datos\"}} en vez de inventar."
        )

        # Mercados REALES del bookmaker para este partido, con su cuota exacta.
        # Se piden ANTES de preguntar a la IA (no despues) porque su lista es
        # la que dice que jugadores tienen prop publicado: sin ella no se puede
        # dar a la IA la forma de esos jugadores.
        markets_reales = []
        try:
            from cuotas_doradobet import mercados_reales as _mr

            markets_reales = _mr(
                p["sport"], p["home_name"], p["away_name"], p["date"],
                ODDS_MINIMA, ODDS_MAXIMA,
            )
        except Exception:
            markets_reales = []

        # Forma reciente de los JUGADORES con prop publicado (statsapi). Cierra
        # el paso 'sacar sus analisis en lo que ha destacado ultimamente': antes
        # solo se daba forma de equipo, y los props de jugador se elegian a ciegas.
        mensaje += _form_jugadores_props(markets_reales)

        texto, modelo = _preguntar_ia(mensaje, p["sport"])
        pick = _parsear_pick_json(texto)

        if not pick and modelo == "SIN_CUOTA":
            # SIN_CUOTA: la IA no respondio (TPD/ITPM de Groq agotado, 402 de
            # You.com). Antes aqui se cortaba el ciclo y el dia se quedaba sin
            # picks. Ahora se sigue con ELECCION CUANTITATIVA sobre los mercados
            # reales que ya tenemos: no gasta un solo token y la cuota es la
            # verdadera del bookmaker, no una estimacion de la IA.
            if not sin_cuota:
                print(
                    "[Dashboard] IA sin cuota: se generan picks por criterio "
                    "cuantitativo sobre los mercados reales",
                    flush=True,
                )
            sin_cuota = True
            errores += 1
            pick = _pick_deterministico(p, markets_reales, label)
            modelo = "cuantitativo (sin IA)"
            if not pick:
                break

        if not pick:
            if modelo == "SIN_CUOTA":
                errores += 1
                sin_cuota = True
                break
            # {"error": "sin datos"} es una respuesta valida de la IA: ese
            # partido no tiene datos, no se reintenta cada ciclo. Un fallo
            # transitorio (respuesta vacia/cortada) SI se reintenta.
            #
            # IMPORTANTE: un fallo de PROVEEDOR (cuota/saldo) NO se guarda como
            # 'sin_datos'. Antes si: si el proveedor devolvia su mensaje de error
            # dentro de un 200 OK, '_es_error_ia' lo reconocia tarde y el partido
            # quedaba descartado 6 h con un motivo que mentia. Al reponer el
            # saldo esos partidos seguian bloqueados. 'sin_datos' debe significar
            # SOLO 'la IA decidio que no hay datos'.
            if texto and '"error"' in texto.lower() and not _es_error_ia(texto):
                db.registrar_descarte(p["event_id"], "sin_datos")
            elif texto:
                # Caida del proveedor: se contabiliza pero NO se cachea, para
                # que el siguiente ciclo reintente cuando el proveedor vuelva.
                from ai.proveedor_fallos import clasificar

                print(
                    f"[Dashboard] Proveedor fallo ({clasificar(texto)}): "
                    f"{p['event_name'][:40]} NO se descarta",
                    flush=True,
                )
                errores += 1
            else:
                errores += 1
            continue

        market = (pick.get("market") or "").strip()

        # RE-ANALISIS CON CUOTAS REALES. En vez de descartar el partido cuando
        # la IA propone un mercado que no se puede resolver (o una cuota fuera
        # de rango), se le vuelve a preguntar CON la lista real de mercados que
        # el sportsbook tiene publicados y su cuota exacta. Asi la IA elige
        # entre commodities que de verdad existen y que se pueden apostar, y el
        # pick sale con la cuota real (no estimada).
        intentos = 0
        while intentos < REINTENTOS_REANALISIS:
            intentos += 1
            motivo = _motivo_rechazo_pick(pick, p, disponibles, market)
            if not motivo:
                break
            if intentos >= REINTENTOS_REANALISIS:
                break
            if motivo == "sin_datos":
                break  # la IA no tiene datos del partido: no insistir
            if motivo == "baja_probabilidad":
                # Se insiste: el usuario pide que si no llega al 70% se busque
                # OTRO pronostico del mismo partido, no que se abandone.
                pass
            mensaje = _mensaje_reanalisis(
                p, label, markets_reales, motivo, intentos
            )
            texto, modelo = _preguntar_ia(mensaje, p["sport"])
            if modelo == "SIN_CUOTA":
                errores += 1
                sin_cuota = True
                break
            nuevo = _parsear_pick_json(texto)
            if not nuevo:
                errores += 1
                break
            pick = nuevo
            market = (pick.get("market") or "").strip()
        else:
            pass

        # Ultimo filtro: si sigue sin ser publicable, se descarta.
        motivo_final = _motivo_rechazo_pick(pick, p, disponibles, market)
        if motivo_final:
            if motivo_final == "no_resoluble":
                rechazados_no_resoluble += 1
            elif motivo_final == "baja_probabilidad":
                rechazados_baja_prob += 1
            elif motivo_final == "prohibido":
                rechazados_prohibido += 1
            elif motivo_final == "cuota":
                rechazados_cuota += 1
            elif motivo_final == "calidad":
                rechazados_calidad += 1
            else:
                errores += 1
            if motivo_final != "sin_datos":
                db.registrar_descarte(p["event_id"], motivo_final)
            continue


        # Cuota REAL (Bet365 via odds-api.io) para el mercado/seleccion elegido.
        # Debe caer en el rango general 1.20-2.50; si no hay cuota real se
        # usa la estimada (que ya paso el filtro de rango).
        cuota_real = _cuota_real_pick(
            p["sport"], p["home_name"], p["away_name"], market,
            str(pick.get("selection", "")), str(pick.get("titulo") or ""),
        )
        if cuota_real is not None:
            if not (ODDS_MINIMA <= cuota_real <= ODDS_MAXIMA):
                rechazados_cuota += 1
                db.registrar_descarte(p["event_id"], "cuota_fuera_rango")
                continue
            odds_final = round(cuota_real, 3)
        else:
            odds_final = pick.get("odds")
            # Props de jugador: las cuotas de la IA suelen ser absurdas (ej.
            # 9.25 por un over 8.5 de ponches). Sin cuota real de Bet365 se
            # rechaza cualquier prop con cuota mayor a 4.00.
            es_prop_jugador = (
                "jugador" in (market or "").lower()
                or "jugador" in str(pick.get("titulo") or "").lower()
            )
            if es_prop_jugador:
                try:
                    if float(odds_final or 0) > 4.0:
                        rechazados_cuota += 1
                        db.registrar_descarte(p["event_id"], "prop_cuota")
                        continue
                except (TypeError, ValueError):
                    pass

        # DOBLE VERIFICACION solo para candidatos GOLDEN (cuota 1.35-1.40):
        # 2 pasadas independientes de la IA; AMBAS deben coincidir. Los
        # STANDARD se publican con la verificacion del analisis principal.
        #
        # GOLDEN = cuota 1.35-1.40 Y mercado de los que mas aciertan. Antes la
        # etiqueta dependia SOLO de la cuota, asi que un 'Aces totales' o un
        # 'Total tiros de esquina' a 1.36 salia como GOLDEN y acababa ANULADO:
        # de 23 golden solo quedo 1 acierto. Un golden tiene que ser un pick
        # que además se pueda RESOLVER y con un mercado de efectividad probada.
        es_golden = _es_golden(odds_final) and peso_mercado(market) >= 80
        verificado = 0
        if es_golden:
            verificado = _doble_verificar_pick(p, market, str(pick.get("selection", "")), label)
            if verificado < VERIFICACIONES_PUBLICAR:
                rechazados_sin_verificar += 1
                db.registrar_descarte(p["event_id"], "sin_verificar")
                continue

        creado = db.create_ai_pick(
            sport=p["sport"],
            sport_label=label,
            event_id=p["event_id"],
            event_name=p["event_name"],
            event_date=p["date"],
            market=market,
            selection=str(pick.get("selection", "")),
            odds=odds_final,
            confidence=pick.get("confidence", "MEDIA"),
            rationale=pick.get("rationale", ""),
            model=modelo or "IA",
            home_name=p["home_name"],
            away_name=p["away_name"],
            home_logo=p["home_logo"],
            away_logo=p["away_logo"],
            titulo=str(pick.get("titulo") or "").strip() or str(pick.get("selection", "")),
            league=p.get("league"),
            stats=json.dumps(
                [str(s) for s in (pick.get("stats") or [])[:5]],
                ensure_ascii=False,
            ) if isinstance(pick.get("stats"), list) and pick.get("stats") else None,
            tier=TIER_GOLDEN if es_golden else TIER_STANDARD,
            verificado=verificado,
        )
        if creado:
            generados += 1
            mercados_usados.add(_market_norm(market))

    stats_out = {
        "partidos": len(partidos),
        "generados": generados,
        "sin_cuota_ia": sin_cuota,
        "meta_min_juegos": MIN_JUEGOS_MANANA,
        "omitidos_ya_con_pick": omitidos,
        "omitidos_descartados": omitidos_descartados,
        "errores": errores,
        "rechazados_cuota": rechazados_cuota,
        "rechazados_calidad": rechazados_calidad,
        "rechazados_prohibido": rechazados_prohibido,
        "rechazados_sin_verificar": rechazados_sin_verificar,
        "rechazados_no_resoluble": rechazados_no_resoluble,
        "rechazados_baja_probabilidad": rechazados_baja_prob,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return stats_out


# ============================================================
# RESOLUCION DE PICKS (ACIERTO / FALLO)
# ============================================================

MLB_BASE = "https://statsapi.mlb.com/api/v1"
_MLB_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 3SIXTYBETS/1.0)"}


def _mlb_get(path: str, params=None):
    import requests

    r = requests.get(
        f"{MLB_BASE}{path}",
        params=params or {},
        headers=_MLB_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _mlb_gamepk(pick: dict):
    """gamePk de statsapi.mlb.com para el evento del pick (±1 dia)."""
    from backend.player_stats import mlb_api

    try:
        dt = datetime.fromisoformat(
            str(pick.get("eventDate") or "").replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None
    try:
        tid = mlb_api.get_team_id(pick.get("homeName") or "") or mlb_api.get_team_id(
            pick.get("awayName") or ""
        )
    except Exception:
        tid = None
    if not tid:
        return None
    fecha = dt.date()
    for delta in (0, -1, 1):
        try:
            data = _mlb_get(
                "/schedule",
                {"sportId": 1, "teamId": tid, "date": (fecha + timedelta(days=delta)).isoformat()},
            )
        except Exception:
            continue
        for d in data.get("dates") or []:
            for g in d.get("games") or []:
                if g.get("gamePk"):
                    return g.get("gamePk")
    return None


ESPN_NFL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"


def _nfl_boxscore(event_id):
    """Boxscore de ESPN: lista de grupos con atletas y sus stats."""
    try:
        import requests

        r = requests.get(f"{ESPN_NFL}/summary", params={"event": event_id},
                         headers={"User-Agent": HLS_USER_AGENT}, timeout=25)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("boxscore") or {}
    except Exception:
        return None


# Claves de ESPN -> tipo de mercado que resuelven.
_NFL_CLAVES = {
    "passingYards": "pasadas",
    "passingTouchdowns": "pasadas_td",
    "interceptions": "intercepciones",
    "rushingYards": "carrera",
    "rushingTouchdowns": "carrera_td",
    "receptions": "receptiones",
    "receivingYards": "recepcion",
    "receivingTouchdowns": "recepcion_td",
    "totalTackles": "entradas",
    "sacks": "sacks",
    "totalPoints": "puntos",
    "fieldGoalsMade/fieldGoalAttempts": "faltos",
}


def _resolver_prop_nfl(pick: dict):
    """Resuelve props de JUGADOR de NFL con el boxscore real de ESPN.

    MLB usa statsapi.mlb.com/game/{pk}/boxscore; el equivalente publico de NFL
    es el summary de ESPN, que trae boxscore.players[].statistics[].athletes[]
    con las claves de cada jugador (passingYards, rushingYards, receptiones,
    sacks, intercepciones...).

    Devuelve 'ACIERTO'/'FALLO' o None si el dato no esta. Nunca adivina.
    """
    if pick.get("sport") != "nfl":
        return None
    texto = (f"{pick.get('market') or ''} {pick.get('titulo') or ''} "
             f"{pick.get('selection') or ''}").lower()
    if "jugador" not in texto and "kicker" not in texto and "receptor" not in texto:
        return None

    # Reusa el parser de linea de los props de MLB (soporta 'over 0.5' y '1+').
    match = re.search(r"(over|under)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto)
    if match:
        es_over = match.group(1) == "over"
        linea = float(match.group(2).replace(",", "."))
    else:
        match = re.search(r"\b([0-9]+)\s*\+", texto)
        if not match:
            return None
        es_over = True
        linea = max(0.0, float(match.group(1)) - 1.0)

    jugador = str(pick.get("titulo") or "").split(":")[0].strip()
    jugador = re.split(r"\d|\bover\b|\bunder\b|\+", jugador)[0].strip()
    if not jugador or len(jugador) < 3:
        return None
    objetivo = set(_norm_texto(jugador).split())

    # Buscar el partido por la ventana temporal del propio modulo de partidos.
    candidatos = [p for p in _partidos_hoy() if p.get("sport") == "nfl"]
    for p in candidatos:
        box = _nfl_boxscore(p.get("event_id"))
        if not box:
            continue
        encontrado = None
        for bloque in box.get("players") or []:
            for grupo in bloque.get("statistics") or []:
                claves = grupo.get("keys") or []
                for at in grupo.get("athletes") or []:
                    nombre = _norm_texto((at.get("athlete") or {}).get("displayName") or "")
                    if objetivo and objetivo.issubset(set(nombre.split())):
                        if encontrado is None:
                            encontrado = {}
                        for i, k in enumerate(claves):
                            if i < len(at.get("stats") or []):
                                encontrado[k] = at["stats"][i]
        if not encontrado:
            continue

        for clave, _tipo in _NFL_CLAVES.items():
            if clave not in encontrado:
                continue
            try:
                valor = float(str(encontrado[clave]).split("/")[0])
            except (TypeError, ValueError):
                continue
            return ("ACIERTO" if valor > linea else "FALLO") if es_over else \
                   ("ACIERTO" if valor < linea else "FALLO")
    return None


def _resolver_prop_mlb(pick: dict):
    """Resuelve props de JUGADOR de MLB con el boxscore real (statsapi).

    Cubre: hits, ponches/strikeouts (lanzador o bateador) y home runs.
    Devuelve ACIERTO/FALLO o None si no es resoluble. Si el jugador NO
    aparece en el boxscore del partido, devuelve FALLO (sin datos no hay
    acierto) — evita que la IA lo adivine con solo el marcador del equipo.
    """
    if pick.get("sport") != "mlb":
        return None
    market = (pick.get("market") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    texto = f"{market} {titulo} {(pick.get('selection') or '').lower()}"
    if "jugador" not in texto and " del jugador" not in texto:
        return None

    # Dos formatos de linea, según de dónde venga el pick:
    #   'over 0.5' / 'under 4.5'  -> forma que usaba la IA
    #   '1+' / '2+'                -> forma que publica Doradobet en sus props
    #                              ('Kyle Schwarber hits 1+ @ 1.588')
    # Antes solo se aceptaba la primera, asi que un prop con la cuota REAL del
    # bookmaker devolvia None y el pick acababa ANULADO.
    match = re.search(r"(over|under)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto)
    if match:
        es_over = match.group(1) == "over"
        linea = float(match.group(2).replace(",", "."))
    else:
        match = re.search(r"\b([0-9]+)\s*\+", texto)
        if not match:
            return None
        # 'N+' significa N o mas, es decir over de (N-1).
        es_over = True
        linea = max(0.0, float(match.group(1)) - 1.0)

    # Nombre del jugador. El resolver Historically lo sacaba de lo que habia
    # antes de ':' ('Kyle Schwarber: Over 0.5 HR'). Los props de Doradobet
    # traen otro formato ('Kyle Schwarber hits totales 1+'), asi que se toman
    # las palabras iniciales hasta el primer numero o signo +, y se descartan
    # las palabras que son el tipo de stat.
    jugador = str(pick.get("titulo") or "").split(":")[0].strip()
    if not jugador or len(jugador) < 4:
        jugador = str(pick.get("selection") or "").strip()
    jugador = re.split(r"\d|\bover\b|\bunder\b|\+", jugador)[0].strip()
    # Quitar el tipo de stat si quedo pegado ('Kyle Schwarber hits totales').
    for _tipo in ("hits totales", "hits", "home runs", "strikeouts",
                  "bases totales", "bases", "hr totales"):
        if _norm_texto(jugador).endswith(_tipo):
            jugador = _norm_texto(jugador)[: -len(_tipo)].strip()
            break
    if not jugador or len(jugador) < 4:
        return None
    jt = set(_norm_texto(jugador).split())

    pk = _mlb_gamepk(pick)
    if not pk:
        return None
    try:
        box = _mlb_get(f"/game/{pk}/boxscore")
    except Exception:
        return None

    jugador_stats = None
    for side in ("away", "home"):
        team = (box.get("teams") or {}).get(side) or {}
        for info in (team.get("players") or {}).values():
            full = _norm_texto((info.get("person") or {}).get("fullName") or "")
            pt = set(full.split())
            if jt and jt.issubset(pt):
                jugador_stats = info
                break
        if jugador_stats:
            break

    if not jugador_stats:
        # El jugador no participo en ese partido: el pick es FALLO
        return "FALLO"

    stats = jugador_stats.get("stats") or {}
    batting = stats.get("batting") or {}
    pitching = stats.get("pitching") or {}
    es_pitcher = (
        ((jugador_stats.get("position") or {}).get("abbreviation") == "P")
        or bool(pitching.get("gamesPlayed"))
    )

    valor = None
    if any(k in texto for k in ("ponch", "strikeout", "so ")):
        valor = pitching.get("strikeOuts") if es_pitcher else batting.get("strikeOuts")
    elif "hit" in texto:
        valor = batting.get("hits")
    elif any(k in texto for k in ("home run", "hr", "cuadrangular")):
        valor = batting.get("homeRuns")
    if valor is None:
        return None
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return None

    return ("ACIERTO" if valor > linea else "FALLO") if es_over else \
           ("ACIERTO" if valor < linea else "FALLO")


def _resolver_stats_sofascore(pick: dict):
    """Resuelve over/under de TARJETAS y CORNERS de futbol.

    El marcador global no determina estos mercados (antes la IA adivinaba).

    ANTES usaba la API de Sofascore, que hoy devuelve 403: la funcion devolvia
    None SIEMPRE, el pick se quedaba PENDIENTE y a las 72h se marcaba ANULADO.
    Medido: 16 picks de corners seguidos con 0 aciertos, algo que estadisticamente
    casi no puede ser real. Ahora usa FotMob (responde 200).

    Si FotMob no trae el dato se devuelve None igual que antes: el pick NO se
    resuelve. Nunca se inventa un numero.
    """
    if pick.get("sport") != "soccer":
        return None
    market = (pick.get("market") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    texto = f"{market} {titulo} {(pick.get('selection') or '').lower()}"

    if "tarjeta" in texto or "card" in texto:
        metricas = ("yellow_cards", "red_cards")
    elif "corner" in texto or "esquina" in texto:
        metricas = ("corners",)
    else:
        return None

    match = re.search(
        r"(over|m[\u00e1a]s de|under|menos de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto
    )
    if not match:
        return None
    es_over = match.group(1).startswith(("over", "m"))
    try:
        linea = float(match.group(2).replace(",", "."))
    except ValueError:
        return None

    try:
        from backend.apuestas import fotmob_stats as fstats
    except Exception:
        return None

    # "Primera mitad" se pide a Periods.FirstHalf, que FotMob trae aparte.
    primero = ("primera mitad" in texto or "1\u00aa mitad" in texto
               or "1a mitad" in texto)
    periodo = "FirstHalf" if primero else "All"

    total = 0
    encontrado = False
    for metrica in metricas:
        t = fstats.total_estadistica_de_pick(pick, metrica, periodo)
        if t is not None:
            total += t
            encontrado = True
    if not encontrado:
        return None

    return ("ACIERTO" if total > linea else "FALLO") if es_over else \
           ("ACIERTO" if total < linea else "FALLO")


MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre",
    12: "diciembre",
}


def _verificar_acierto_con_fuentes(pick: dict, marcador: str):
    """Verificacion ESTRICTA del resultado: N pasadas independientes de la IA.

    Cada pasada pide verificar el resultado en sitios externos
    (sofascore.com / flashscore.com / fotmob.com) con la consulta del tipo
    'STATS {local} VS {visitante} HOY (dia) DE (mes)'. Solo devuelve ACIERTO o
    FALLO si TODAS las pasadas coinciden; si alguna discrepa devuelve None
    (el pick queda PENDIENTE para re-verificar en el proximo ciclo).
    """
    ahora = _hora_nicaragua()
    equipos = pick.get("eventName") or ""
    local = pick.get("homeName") or ""
    visitante = pick.get("awayName") or ""

    mensaje = (
        f"TIPO: STATS {local} VS {visitante} HOY ({ahora.day}) DE "
        f"{MESES_ES.get(ahora.month, ahora.month)}.\n"
        f"Partido: {equipos} (deporte {pick.get('sportLabel', '')}).\n"
        f"Pick: mercado '{pick.get('market')}' - seleccion '{pick.get('selection')}'.\n"
        f"Marcador final que tenemos registrado: {marcador}.\n\n"
        "Verifica el resultado FINAL de ese partido en sitios externos como "
        "sofascore.com, flashscore.com y fotmob.com. Comprueba si TODAS las "
        "fuentes coinciden con el marcador registrado y si el pick resulto "
        "ganador. Responde SOLO una palabra: ACIERTO o FALLO. Si el marcador "
        "no coincide con las fuentes externas, responde DISCREPANTE."
    )

    votos = []
    for i in range(VERIFICACIONES_ACIERTO):
        texto, _ = _preguntar_ia(mensaje, system_prompt=PROMPT_VERIFICACION, buscar_web=False)
        upper = (texto or "").upper()
        if "ACIERTO" in upper:
            votos.append("ACIERTO")
        elif "FALLO" in upper:
            votos.append("FALLO")
        else:
            votos.append(None)
        if len(set(v for v in votos if v)) > 1:
            print(
                f"[Dashboard] Verificacion {i + 1}/{VERIFICACIONES_ACIERTO} "
                f"discrepante para pick {pick.get('id')}: {votos}",
                flush=True,
            )
            return None  # fuentes no coinciden: no marcar nada

    if any(v is None for v in votos):
        return None  # alguna pasada no fue concluyente
    return votos[0]


def _resolver_pick_con_ia(pick: dict):
    """Pregunta a la IA si el pick fue ACIERTO o FALLO con el resultado final."""
    import sports

    try:
        detail = sports.get_game_detail(pick["sport"], pick["eventId"])
    except Exception:
        return None

    teams = detail.get("teams", [])
    if len(teams) < 2 or detail.get("state") != "post":
        return None

    marcador = " vs ".join(
        f"{t.get('name', '?')} {t.get('score', '-')}" for t in teams
    )
    mensaje = (
        f"Pick realizado: mercado '{pick.get('market')}' - seleccion '{pick.get('selection')}'.\n"
        f"Partido: {pick.get('eventName')} (deporte {pick.get('sportLabel', '')}).\n"
        f"Resultado final: {marcador}.\n\n"
        f"Con ese resultado final, Â¿el pick fue ACIERTO o FALLO?\n"
        f"Responde SOLO una palabra: ACIERTO o FALLO. Si el mercado no se puede "
        f"determinar con ese marcador, responde INDETERMINADO."
    )

    texto, _ = _preguntar_ia(mensaje, system_prompt=PROMPT_VERIFICACION, buscar_web=False)
    if not texto:
        return None
    upper = texto.upper()
    if "INDETERMINADO" in upper:
        return None
    if "ACIERTO" in upper:
        return "ACIERTO"
    if "FALLO" in upper:
        return "FALLO"
    return None


def backfill_picks_metadata() -> int:
    """Repara picks viejos sin nombres/logos de equipos usando los datos de hoy.

    Los picks generados antes de la correccion quedaron con home_name NULL y
    por eso el frontend mostraba '?'. Esto los actualiza con la info de ESPN.
    """
    viejos = db.picks_sin_equipo() or []
    if not viejos:
        return 0

    por_evento = {p["event_id"]: p for p in _partidos_hoy()}
    reparados = 0
    for pick in viejos:
        partido = por_evento.get(pick["eventId"])
        if not partido:
            continue
        if db.update_pick_metadata(
            pick["id"],
            partido["home_name"],
            partido["away_name"],
            partido["home_logo"],
            partido["away_logo"],
            partido.get("league"),
        ):
            reparados += 1
    return reparados


def _resolver_deterministico(pick: dict, detail: dict):
    """Resuelve el pick con reglas directas sobre el marcador final (sin IA).

    Cubre los mercados determinables: ganador, BTTS, over/under totales,
    doble oportunidad, sin empate, equipo total de goles. Devuelve
    ACIERTO/FALLO o None si el mercado no es determinable con el marcador.
    """
    teams = detail.get("teams") or []
    if len(teams) < 2:
        return None

    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
    try:
        hs = float(home.get("score"))
        as_ = float(away.get("score"))
    except (TypeError, ValueError):
        return None

    total = hs + as_
    market = (pick.get("market") or "").lower()
    sel = (pick.get("selection") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    texto = f"{market} {titulo} {sel}"

    home_name, away_name = (home.get("name") or "").lower(), (away.get("name") or "").lower()

    def _equipo_de(sel_txt: str):
        if home_name and home_name in sel_txt:
            return home
        if away_name and away_name in sel_txt:
            return away
        return None

    # ---- Ganador / ML / "X gana" ----
    if any(k in texto for k in ("ganador", " gana", "moneyline", " ml", "winner")) and \
       "primera mitad" not in texto and "prorroga" not in texto and "1er" not in texto:
        if hs == as_:
            return "FALLO"  # empate: solo gana quien apostó empate, que no damos
        ganador = home if hs > as_ else away
        elegido = _equipo_de(sel + " " + titulo)
        if elegido is None:
            return None
        return "ACIERTO" if elegido.get("name") == ganador.get("name") else "FALLO"

    # ---- Ambos equipos marcan / BTTS ----
    if "ambos" in texto and ("marcan" in texto or "anotan" in texto) or "btts" in texto:
        si = (hs > 0) and (as_ > 0)
        eligio_si = not sel.strip().startswith("no")
        return "ACIERTO" if si == eligio_si else "FALLO"

    # ---- Apuesta sin empate (Draw No Bet) ----
    if "sin empate" in texto:
        if hs == as_:
            return "FALLO"
        elegido = _equipo_de(sel + " " + titulo)
        if elegido is None:
            return None
        ganador = home if hs > as_ else away
        return "ACIERTO" if elegido.get("name") == ganador.get("name") else "FALLO"

    # ---- Doble oportunidad ----
    if "doble oportunidad" in texto or "doble oport" in texto:
        codigo = sel.replace(" ", "")
        if hs == as_:
            return "ACIERTO" if codigo in ("1x", "x2") else "FALLO"
        gana_local = hs > as_
        if codigo == "1x":
            return "ACIERTO" if gana_local else "FALLO"
        if codigo == "x2":
            return "ACIERTO" if not gana_local else "FALLO"
        if codigo == "12":
            return "ACIERTO"
        return None

    # ---- Over/Under de goles/puntos/corners (total o de un equipo) ----
    match = re.search(
        r"(over|m[áa]s de|under|menos de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto
    )
    if match:
        tipo = match.group(1).lower()
        try:
            linea = float(match.group(2).replace(",", "."))
        except ValueError:
            return None
        es_over = tipo.startswith(("over", "m"))
        # Si es un total de un equipo concreto, usar su marcador
        if ("equipo" in texto or "total de goles" in texto or "totales" in texto) and \
           "prorroga" not in texto:
            equipo = _equipo_de(sel + " " + titulo)
            if equipo is not None:
                try:
                    valor = float(equipo.get("score"))
                except (TypeError, ValueError):
                    return None
                return ("ACIERTO" if valor > linea else "FALLO") if es_over else \
                       ("ACIERTO" if valor < linea else "FALLO")
        return ("ACIERTO" if total > linea else "FALLO") if es_over else \
               ("ACIERTO" if total < linea else "FALLO")

    return None


def _resolver_por_mitades(pick: dict, detail: dict):
    """Resuelve mercados de MITAD con el marcador por periodos de ESPN.

    Cubre 'Gana cualquier mitad' / 'Gana ambas mitades' / '1x2 1a mitad'. Son
    mercados que el bookmaker publica y con cuotas muy bajas (1.27 el de
    Nacional), pero el marcador FINAL no los determina: hacen falta los
    marcadores de cada periodo, que ESPN expone en 'linescores'
    (competitor.linescores[i] = goles de la mitad i).

    Sin esto, todos esos picks acababan ANULADOS (medido: 6 de 15 en produccion).
    Si ESPN no trae linescores se devuelve None y el pick queda como estaba.
    """
    texto = " ".join([
        str(pick.get("market") or ""),
        str(pick.get("titulo") or ""),
        str(pick.get("selection") or ""),
    ]).lower()
    # 'Gana cualquier mitad' y 'Gana ambas mitades' son mercados DISTINTOS y el
    # titulo de cualquiera de los dos contiene la palabra 'mitad'. Se decide
    # primero por 'ambas' (exige ganar las dos) y solo si no aparece se trata
    # como 'cualquier' (basta con ganar una).
    es_ambas = "ambas" in texto
    es_cualquier = (not es_ambas) and ("cualquier" in texto or "alguna" in texto)
    # Solo se mira la 1a mitad si el mercado lo dice de forma explicita
    # ('1a mitad - 1x2', '1x2 1a mitad'). Antes se detectaba con un '1' suelto,
    # que tambien aparece en cualquier otra parte del texto.
    es_primera = bool(
        re.search(r"\b1\s*(?:a|ª|er)?\s*(?:mitad|half)\b", texto)
        or "1x2 1" in texto
    )

    if not (es_ambas or es_cualquier or es_primera):
        return None

    teams = detail.get("teams") or []
    if len(teams) < 2:
        return None

    # MarCADOR POR PERIODO: cada competidor trae linescores con un valor por
    # periodo. Se normalizan a enteros.
    periodos = []
    for t in teams:
        ls = t.get("linescores") or []
        vals = []
        for p in ls:
            if isinstance(p, dict):
                v = p.get("displayValue", p.get("value"))
            else:
                v = p
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                vals.append(0.0)
        periodos.append(vals)

    if not periodos[0] or not periodos[1]:
        return None
    n = min(len(periodos[0]), len(periodos[1]))
    if n == 0:
        return None

    def _lado():
        """Indice del equipo (0 local / 1 visitante) al que se apostó.

        No basta con 'el nombre del equipo esta en el titulo': el pick suele
        llevar el nombre CORTO ('Nacional gana cualquier mitad') y el equipo se
        llama 'Atlético Nacional'. Se comparan palabras sueltas para que el
        apellido ('nacional') baste, con cuidado de no emparejar por palabras
        vacías.
        """
        h = (teams[0].get("name") or "").lower()
        a = (teams[1].get("name") or "").lower()
        cand = f"{pick.get('titulo') or ''} {pick.get('selection') or ''}".lower()
        for nombre, i in ((h, 0), (a, 1)):
            if not nombre:
                continue
            if nombre in cand:
                return i
            #.Any() sobre palabras con 4+ letras: 'nacional' identifica a
            # 'atlético nacional' sin que el titulo repita el nombre entero.
            palabras = [w for w in _norm_texto(nombre).split() if len(w) >= 4]
            if palabras and any(w in _norm_texto(cand) for w in palabras):
                return i
        return None

    lado = _lado()
    if lado is None:
        return None

    # Solo la 1a mitad: compara el primer periodo.
    if es_primera and not (es_ambas or es_cualquier):
        return "ACIERTO" if periodos[lado][0] > periodos[1 - lado][0] else "FALLO"

    mitades = [(periodos[0][i], periodos[1][i]) for i in range(n)]
    gana_mitades = mitades if lado == 0 else [(b, a) for a, b in mitades]
    propias = [g for g, _ in gana_mitades]
    rivales = [r for _, r in gana_mitades]

    if es_ambas:
        # Gana las DOS mitades.
        return "ACIERTO" if all(p > r for p, r in zip(propias, rivales)) else "FALLO"
    if es_cualquier:
        # Gana AL MENOS UNA mitad. Los empates a mitad no cuentan como ganar.
        gana_alguna = any(p > r for p, r in zip(propias, rivales))
        sel = (str(pick.get("selection") or "")).strip().lower()
        quiere_si = not sel.startswith(("no", "nunca", "-"))
        return "ACIERTO" if gana_alguna == quiere_si else "FALLO"

    return None


def _detalle_resolucion(pick: dict):
    """Obtiene el detalle del partido para resolver el pick.

    A diferencia de get_game_detail (que para soccer solo busca en el
    scoreboard de HOY), usa la liga guardada en el pick para consultar el
    summary de ESPN aunque el partido sea de dias anteriores.

    El summary por evento funciona para TODOS los deportes de ESPN, no solo
    futbol. Antes solo el Futbol lo usaba; MLB/NBA/tenis iban directos a
    get_game_detail, que busca en el scoreboard del dia: al pasar unas horas
    el evento ya no estaba ahi y el pick acababa ANULADO aunque el marcador
    final estuviera disponible en ESPN. Se generaliza porque el endpoint
    responde igual para los cuatro deportes.
    """
    import sports

    sport = pick["sport"]
    eid = pick["eventId"]
    league = pick.get("league")

    # summary por evento: funciona si hay liga guardada y para cualquier
    # deporte de ESPN.
    if league:
        try:
            import requests as http_requests

            from sports import ESPN_BASE, _parse_number

            url = f"{ESPN_BASE}/{sport}/{league}/summary?event={eid}"
            data = http_requests.get(url, timeout=20).json()
            header = data.get("header") or {}
            comps = (header.get("competitions") or [{}])[0]
            state = ((comps.get("status") or {}).get("type") or {}).get("state")
            teams = []
            for c in comps.get("competitors", []):
                # linescores: marcador por periodo. Necesario para los mercados
                # de mitad ('gana cualquier mitad'), que el marcador final no
                # determina. Sin esto llegaban vacios y el pick se anulaba.
                linescores = c.get("linescores") or []
                teams.append({
                    "name": (c.get("team") or {}).get("displayName", "?"),
                    "score": _parse_number(c.get("score")),
                    "homeAway": c.get("homeAway"),
                    "linescores": linescores,
                })
            # Si ESPN responde pero sin equipos, el evento no existe para esa
            # liga: se cae al scoreboard del dia en vez de devolver vacio.
            if state and len(teams) >= 2:
                if not any(t.get("linescores") for t in teams):
                    # scoreboard del dia: puede traer linescores, se acepta igual
                    for t in teams:
                        t["linescores"] = []
                return {"state": state, "teams": teams}
        except Exception:
            pass

    # Sin liga guardada o sin respuesta valida: scoreboard del dia.
    return sports.get_game_detail(sport, eid)


# ============================================================
# SOFASCORE (verificacion OBLIGATORIA de marcadores, 3ra fuente)
# ============================================================

SOFASCORE_API = "https://api.sofascore.com/api/v1"
_SOFASCORE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}
_sofascore_cache = {}  # clave -> (timestamp, data)
_SOFASCORE_TTL = 600  # 10 min
_sofascore_bloqueado_hasta = 0  # backoff ante 403/429 de Cloudflare
_SOFASCORE_BACKOFF = 900  # 15 min


def _sofascore_get(path: str, params=None):
    """GET contra api.sofascore.com con cache, backoff y registro de salud.

    Sofascore protege su API con Cloudflare validando la huella TLS: las
    peticiones se hacen con curl_cffi impersonando Chrome (si esta
    disponible); fallback a requests normal (suele dar 403).
    """
    global _sofascore_bloqueado_hasta
    ahora = time.time()
    if ahora < _sofascore_bloqueado_hasta:
        return None
    cache_key = f"{path}:{params.get('q', '') if params else ''}"
    cached = _sofascore_cache.get(cache_key)
    if cached and ahora - cached[0] < _SOFASCORE_TTL:
        return cached[1]
    url = f"{SOFASCORE_API}{path}"
    data = None
    # 1) curl_cffi con TLS de Chrome real (pasa el antibot de Cloudflare)
    try:
        from curl_cffi import requests as curl_requests

        r = curl_requests.get(
            url,
            params=params or {},
            impersonate="chrome124",
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
    except Exception as exc:
        _salud["sofascore_estado"] = f"curl_cffi: {exc}"
    # 2) Fallback: requests normal (por si curl_cffi no esta instalado)
    if data is None:
        try:
            import requests

            r = requests.get(
                url,
                params=params or {},
                headers=_SOFASCORE_HEADERS,
                timeout=10,
            )
            if r.status_code in (403, 429, 451):
                _sofascore_bloqueado_hasta = ahora + _SOFASCORE_BACKOFF
                _salud["sofascore_estado"] = f"bloqueado ({r.status_code})"
                print(
                    f"[Dashboard] Sofascore bloqueado ({r.status_code}); pausa 15 min",
                    flush=True,
                )
                return None
            if r.status_code == 200:
                data = r.json()
        except Exception as exc:
            _salud["sofascore_estado"] = f"error: {exc}"
    if data is not None:
        _sofascore_cache[cache_key] = (ahora, data)
        _salud["sofascore_estado"] = "ok"
    return data


def _coincide_nombres(nombre: str, team: dict) -> bool:
    a = _norm_texto(nombre)
    b = _norm_texto((team or {}).get("name") or (team or {}).get("fullName") or "")
    return bool(a) and bool(b) and (a in b or b in a)


def _sofascore_encontrar_evento(pick: dict):
    """Busca el evento del pick en Sofascore (validando fecha ±12h).

    Devuelve el dict del evento (con homeScore/awayScore/status/startTimestamp)
    o None si no lo encuentra.
    """
    home = pick.get("homeName") or ""
    away = pick.get("awayName") or ""
    if not home or not away:
        return None
    try:
        fecha_pick = datetime.fromisoformat(
            str(pick.get("eventDate") or "").replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        fecha_pick = None

    data = _sofascore_get("/search/all", {"q": f"{home} {away}"})
    for item in (data or {}).get("results") or []:
        if item.get("type") != "event":
            continue
        ev = item.get("entity") or item
        start = ev.get("startTimestamp")
        if fecha_pick is not None and start:
            try:
                fecha_ev = datetime.fromtimestamp(int(start), timezone.utc)
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            if abs((fecha_ev - fecha_pick).total_seconds()) > 12 * 3600:
                continue  # mismo duelo pero OTRA fecha: descartar
        h_team = ev.get("homeTeam") or {}
        a_team = ev.get("awayTeam") or {}
        # Solo importa que esten los DOS equipos del pick, sin importar quien
        # es local (el mismo duelo aparece en ambos ordenes en la busqueda).
        if not (
            (_coincide_nombres(home, h_team) or _coincide_nombres(home, a_team))
            and (_coincide_nombres(away, h_team) or _coincide_nombres(away, a_team))
        ):
            continue
        return ev
    return None


def _sofascore_stat_total(pick: dict, claves_stat):
    """Total (local+visitante) de una estadistica del partido via Sofascore.

    claves_stat: tuplas de substrings del key de la estadistica
    (ej. ('yellowcards', 'redcards') para tarjetas, ('corners',) para corners).
    Solo cuenta si el partido ya termino. Devuelve int o None.
    """
    ev = _sofascore_encontrar_evento(pick)
    if not ev:
        return None
    if ((ev.get("status") or {}).get("type") or "") != "finished":
        return None
    st = _sofascore_get(f"/event/{ev.get('id')}/statistics") or {}
    for grp in st.get("statistics") or []:
        if (grp.get("period") or "").upper() != "ALL":
            continue
        total = 0
        encontrado = False
        for gi in grp.get("groups") or []:
            for si in gi.get("statisticsItems") or []:
                k = (si.get("key") or "").lower()
                if any(c in k for c in claves_stat):
                    try:
                        total += int(si.get("home") or 0) + int(si.get("away") or 0)
                        encontrado = True
                    except (TypeError, ValueError):
                        pass
        if encontrado:
            return total
    return None


def _detalle_desde_sofascore(pick: dict):
    """Marcador desde api.sofascore.com para el evento del pick.

    Devuelve el detalle en el mismo formato que ESPN/odds-api:
    {"state": "post"|"in", "teams": [{name, score, homeAway}, ...]}
    """
    home = pick.get("homeName") or ""
    away = pick.get("awayName") or ""
    if not home or not away:
        return None
    ev = _sofascore_encontrar_evento(pick)
    if not ev:
        return None
    hs = (ev.get("homeScore") or {}).get("current")
    as_ = (ev.get("awayScore") or {}).get("current")
    # En /search/all el score viene como 'display' (no 'current')
    if hs is None:
        hs = (ev.get("homeScore") or {}).get("display")
    if as_ is None:
        as_ = (ev.get("awayScore") or {}).get("display")
    if hs is None or as_ is None:
        return None
    estado = (ev.get("status") or {}).get("type") or ""
    if estado == "finished":
        state = "post"
    elif estado == "inprogress":
        state = "in"
    else:
        state = "pre"
    h_team = ev.get("homeTeam") or {}
    a_team = ev.get("awayTeam") or {}
    # Reorientar al orden del pick (home/away de NUESTRA BD) para que el
    # resolutor deterministico siga funcionando igual.
    local_en_sofascore = _coincide_nombres(home, h_team)
    home_score = hs if local_en_sofascore else as_
    away_score = as_ if local_en_sofascore else hs
    return {
        "state": state,
        "teams": [
            {"name": home, "score": home_score, "homeAway": "home"},
            {"name": away, "score": away_score, "homeAway": "away"},
        ],
    }


def _marcador_discrepa(d1: dict, d2: dict) -> bool:
    """True si los dos detalles dan marcadores finales DISTINTOS."""
    def _score_map(d):
        m = {}
        for t in d.get("teams") or []:
            try:
                m[t.get("homeAway")] = float(t.get("score"))
            except (TypeError, ValueError):
                return None
        return m if "home" in m and "away" in m else None

    s1, s2 = _score_map(d1), _score_map(d2)
    if not s1 or not s2:
        return False
    return s1["home"] != s2["home"] or s1["away"] != s2["away"]


def _detalle_desde_fotmob(pick: dict):
    """Marcador final desde FotMob, en el MISMO formato que ESPN.

    ESPN es la fuente principal de resultados; si el evento ya no aparece alli
    (deja de estar en el scoreboard del dia a las pocas horas) el pick se queda
    PENDIENTE para siempre. FotMob cubre ese hueco y no anade dependencias
    nuevas: ya se usa para corners/tarjetas.

    odds-api.io hacia ese mismo papel pero lleva semanas con 401, con lo que en
    la practica nunca llego a resolver nada.

    Devuelve {'state','teams':[{name,score,homeAway}...]} o None.
    """
    try:
        from backend.apuestas import fotmob_stats as fstats
    except Exception:
        return None

    home = pick.get("home_name") or ""
    away = pick.get("away_name") or ""
    if not home or not away:
        return None

    info = fstats.partido_por_equipos(home, away, pick.get("eventDate"))
    if not info or not info.get("finished"):
        return None
    if info.get("home_score") is None or info.get("away_score") is None:
        return None

    # Reorientar al orden del pick: FotMob puede tener los equipos al reves.
    # _coincide_nombres espera dicts de equipo; aqui tengo nombres sueltos,
    # asi que se comparan con la misma normalizacion de texto.
    local_en_fotmob = (
        _norm_texto(home) == _norm_texto(info.get("home_name") or "")
    )
    home_score = info["home_score"] if local_en_fotmob else info["away_score"]
    away_score = info["away_score"] if local_en_fotmob else info["home_score"]
    return {
        "state": "post",
        "teams": [
            {"name": home, "score": home_score, "homeAway": "home"},
            {"name": away, "score": away_score, "homeAway": "away"},
        ],
    }


def _detalle_desde_oddsapi(pick: dict):
    """Fallback: marcador final desde odds-api.io (eventos settled incluyen scores).

    Permite resolver picks de partidos que ya no aparecen en ESPN.
    IMPORTANTE: solo cuenta el evento si su FECHA coincide con la del pick
    (±12h). Sin ese guard, el matching por nombre de equipos resolvía el pick
    de HOY con el resultado del mismo duelo de AYER (o de hace una semana).
    """
    slug = ODDS_SPORT_SLUGS.get(pick["sport"])
    if not slug:
        return None

    def _fecha_evento_pick():
        try:
            return datetime.fromisoformat(
                str(pick.get("eventDate") or "").replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            return None

    fecha_pick = _fecha_evento_pick()

    eventos = _odds_eventos(slug)

    def _coincide(a: str, b: str) -> bool:
        a, b = _norm_texto(a), _norm_texto(b)
        return bool(a) and bool(b) and (a == b or a in b or b in a)

    for e in eventos:
        if e.get("status") != "settled":
            continue
        scores = e.get("scores") or {}
        if scores.get("home") is None or scores.get("away") is None:
            continue
        # Guard de fecha: el evento debe ser el MISMO duelo (misma fecha ±12h)
        if fecha_pick is not None:
            try:
                fecha_evento = datetime.fromisoformat(
                    str(e.get("date", "")).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if abs((fecha_evento - fecha_pick).total_seconds()) > 12 * 3600:
                continue
        if _coincide(e.get("home", ""), pick.get("homeName") or "") and _coincide(
            e.get("away", ""), pick.get("awayName") or ""
        ):
            return {
                "state": "post",
                "teams": [
                    {"name": e["home"], "score": scores["home"], "homeAway": "home"},
                    {"name": e["away"], "score": scores["away"], "homeAway": "away"},
                ],
            }
    return None


def resolver_picks_finalizados() -> dict:
    """Resuelve los picks pendientes cuyos partidos ya terminaron.

    Primero intenta resolucion deterministica con el marcador final; si el
    mercado no es determinable, pregunta a la IA.
    """
    import sports

    pendientes = db.list_picks_pendientes() or []
    resueltos = 0
    for pick in pendientes:
        # Edad del evento y del pick. El orden importa: primero se calcula
        # CUANDO se juega el partido, porque de eso depende que el pick siga
        # siendo vigente.
        try:
            ev = datetime.fromisoformat(str(pick.get("eventDate") or "").replace("Z", "+00:00"))
            horas_evento = (datetime.now(timezone.utc) - ev).total_seconds() / 3600
        except (ValueError, TypeError):
            horas_evento = 0
        try:
            creado = datetime.fromisoformat(pick.get("createdAt") or "")
            horas = (datetime.now(timezone.utc).replace(tzinfo=None) - creado).total_seconds() / 3600
        except (ValueError, TypeError):
            horas = 0

        # El partido AUN NO ha empezado: el pick sigue vivo. Antes se anulaba
        # por antiguedad (>24h) aunque el evento fuera dentro de horas, y el
        # scheduler genera en una ventana de 32h: eso se llevaba por delante
        # picks de manana que todavia no se habian jugado y nunca podian
        # contar como acierto ni como fallo.
        if horas_evento < 0:
            continue

        # Antiguedad: el pick se anula SOLO si ya no hay nada que hacer. Se
        # consulta primero el marcador (abajo) y solo se anula si, habiendo
        # datos, el mercado no se puede decidir. Antes se anulaba por edad
        # (>24h) sin mirar los datos: se llevaba por delante cientos de picks
        # cuyo resultado final SI estaba disponible en ESPN.

        try:
            detail = _detalle_resolucion(pick)
            # Cadena de fallback del marcador: ESPN (principal) -> FotMob ->
            # odds-api. FotMob entra antes que odds-api porque funciona hoy
            # (odds-api lleva semanas con 401 y nunca llego a resolver nada).
            if not detail or detail.get("state") not in ("post", "in"):
                detail = _detalle_desde_fotmob(pick) or detail
            if not detail or detail.get("state") not in ("post", "in"):
                detail = _detalle_desde_oddsapi(pick) or detail
        except Exception:
            continue

        # Verificacion OBLIGATORIA con api.sofascore.com (tercera fuente).
        # Si Sofascore tiene el partido finalizado y su marcador DIFIERE del de
        # ESPN/odds-api, el pick NO se resuelve (queda PENDIENTE para
        # re-verificar en el proximo ciclo). Si ESPN/odds-api no dieron
        # marcador, el de Sofascore se usa directo.
        try:
            sofascore = _detalle_desde_sofascore(pick)
        except Exception:
            sofascore = None
        if sofascore and sofascore.get("state") == "post":
            if detail and detail.get("state") == "post" and _marcador_discrepa(detail, sofascore):
                print(
                    f"[Dashboard] Pick {pick['id']}: discrepancia de marcador entre "
                    f"ESPN/odds-api y Sofascore; queda PENDIENTE",
                    flush=True,
                )
                _salud["marcadores_discrepantes"] = _salud.get("marcadores_discrepantes", 0) + 1
                continue
            if not detail or detail.get("state") != "post":
                detail = sofascore

        if detail.get("state") != "post" or len(detail.get("teams") or []) < 2:
            continue

        # 1) Reglas directas con el marcador (mismo duelo, guard de fecha)
        resultado = _resolver_deterministico(pick, detail)
        if resultado:
            db.update_pick_result(pick["id"], resultado)
            resueltos += 1
            continue

        # 1-bis) Mercados de MITAD: necesitan el marcador por periodos, no el
        # total. Van aparte porque el marcador final no los determina.
        try:
            resultado = _resolver_por_mitades(pick, detail)
        except Exception:
            resultado = None
        if resultado:
            db.update_pick_result(pick["id"], resultado)
            resueltos += 1
            continue

        # 1.5) Mercados con estadistica real disponible:
        #   - props de jugador MLB -> boxscore oficial (statsapi)
        #   - props de jugador NFL -> boxscore de ESPN (mismo patron)
        #   - tarjetas/corners futbol -> estadisticas de Sofascore
        # El marcador global NO determina estos mercados (la IA adivinaba).
        try:
            resultado = (_resolver_prop_mlb(pick) or _resolver_prop_nfl(pick)
                        or _resolver_stats_sofascore(pick))
        except Exception:
            resultado = None
        if resultado:
            db.update_pick_result(pick["id"], resultado)
            resueltos += 1
            continue

        # 2) IA solo si el mercado no es determinable con el marcador.
        #    El resultado de la IA SOLO se acepta si la verificacion estricta
        #    multi-fuente (Sofascore/Flashscore/Fotmob, 6 pasadas) coincide
        #    con el mismo veredicto; si no, el pick queda PENDIENTE.
        ia_resultado = _resolver_pick_con_ia(pick)
        if ia_resultado is None:
            # Hay marcador final pero el mercado no se puede decidir con el
            # (props sin boxscore, mercados sin fuente). A partir de 72h el
            # pick ya no va a mejorar: se anula para no dejarlo eternamente
            # PENDIENTE. Antes este anulado no existia y todos estos picks
            # se acumulaban en PENDIENTE sin llegar nunca a resolverse.
            if horas > 72:
                db.update_pick_result(pick["id"], "ANULADO")
            continue
        marcador = " vs ".join(
            f"{t.get('name', '?')} {t.get('score', '-')}"
            for t in (detail.get("teams") or [])
        )
        verificado = _verificar_acierto_con_fuentes(pick, marcador)
        if verificado is None or verificado != ia_resultado:
            print(
                f"[Dashboard] Pick {pick.get('id')} queda PENDIENTE: "
                f"IA={ia_resultado} verificacion={verificado}",
                flush=True,
            )
            continue
        db.update_pick_result(pick["id"], verificado)
        resueltos += 1
    return {"pendientes": len(pendientes), "resueltos": resueltos}


# ============================================================
# RESUMEN PARA EL DASHBOARD
# ============================================================


def _label_ayer_confusion(pick: dict) -> str | None:
    """Etiqueta para picks de AYER: 'Ayer (HH:MM) LA GENTE SE ESTA CONFUNDIENDO'.

    La hora es la hora a la que se jugo el evento, en hora Nicaragua.
    """
    try:
        dt = datetime.fromisoformat(
            str(pick.get("eventDate") or "").replace("Z", "+00:00")
        )
        local = dt.astimezone(TZ_NICARAGUA)
        return f"Ayer ({local.strftime('%H:%M')}) LA GENTE SE ESTÁ CONFUNDIENDO"
    except (ValueError, TypeError):
        return "AYER LA GENTE SE ESTÁ CONFUNDIENDO"


def aciertos_visibles() -> list:
    """Aciertos que se muestran en el dashboard.

    - Los de HOY siempre.
    - Los de AYER solo hasta las 23:00 hora Nicaragua (con etiqueta 'Ayer').
    - Nunca muestra picks con cuota <= ODDS_MINIMA.
    """
    aciertos = db.list_picks_aciertos_hoy_ayer() or []
    ahora_local = _hora_nicaragua()
    mostrar_ayer = ahora_local.hour < HORA_CORTE_ACERTADOS
    visibles = []
    for p in aciertos:
        if not _pick_calidad_ok(p):
            continue
        # "De ayer" se decide por la fecha en que SE JUGO el evento y tambien
        # por cuando se creo el pick (regla del usuario: al pasar las 12:00,
        # todo lo del dia anterior debe decir AYER).
        es_ayer = False
        try:
            dt_ev = datetime.fromisoformat(
                str(p.get("eventDate") or "").replace("Z", "+00:00")
            )
            es_ayer = dt_ev.astimezone(TZ_NICARAGUA).date() < ahora_local.date()
        except (ValueError, TypeError):
            pass
        if not es_ayer:
            try:
                dt_cr = datetime.fromisoformat(p.get("createdAt") or "")
                es_ayer = (
                    dt_cr.replace(tzinfo=timezone.utc).astimezone(TZ_NICARAGUA).date()
                    < ahora_local.date()
                )
            except (ValueError, TypeError):
                es_ayer = (p.get("pickDate") or "") < ahora_local.date().isoformat()
        if es_ayer and not mostrar_ayer:
            continue
        if es_ayer:
            p["fechaLabel"] = _label_ayer_confusion(p)
        visibles.append(p)
    return visibles


def _evento_vigente(pick: dict) -> bool:
    """True si el evento del pick es de HOY (Nicaragua) o futuro.

    Los pendientes cuyo partido ya paso (ej. '09 sept') salen del dashboard:
    quedan en la BD para el historico, pero no se muestran como 'del dia'.
    """
    fecha_raw = pick.get("eventDate")
    if not fecha_raw:
        return True  # sin fecha no podemos descartarlo
    try:
        dt = datetime.fromisoformat(str(fecha_raw).replace("Z", "+00:00"))
        fecha_evento = dt.astimezone(TZ_NICARAGUA).date()
    except (ValueError, TypeError):
        return True
    return fecha_evento >= _hora_nicaragua().date()


def revisar_picks_hoy(corregir: bool = False) -> dict:
    """Re-verifica TODOS los picks de HOY contra las estadisticas reales.

    - ACIERTO/FALLO: recalcula el resultado del mercado con el marcador/stats
      del partido (ESPN; fallback odds-api.io) y marca SOSPECHOSO si no
      coincide con el resultado guardado.
    - PENDIENTE: si el partido esta EN VIVO reporta el marcador actual y si el
      pick va ganando o perdiendo; si ya termino y sigue pendiente, alerta.
    - corregir=True: corrige automaticamente los resultados mal guardados
      (partido post: aplica el recalculado; en vivo: reinicia a PENDIENTE
      para que el resolver lo procese al terminar).
    """
    picks = db.list_picks_hoy() or []
    revisados, sospechosos, en_vivo = 0, [], []

    for pick in picks:
        estado = pick.get("result")
        try:
            detail = _detalle_resolucion(pick)
            if not detail or detail.get("state") not in ("post", "in"):
                detail = _detalle_desde_oddsapi(pick) or detail
        except Exception:
            continue
        if not detail:
            continue
        state = detail.get("state")
        if not state:
            continue

        revisados += 1
        marcador = " | ".join(
            f"{(t.get('name') or '?')}: {t.get('score')}"
            for t in (detail.get("teams") or [])
        )
        item = {
            "id": pick.get("id"),
            "titulo": pick.get("titulo"),
            "market": pick.get("market"),
            "evento": pick.get("eventName"),
            "estado_guardado": estado,
            "estado_partido": state,
            "marcador": marcador,
            "resultado_recalculado": None,
        }

        try:
            recalculado = _resolver_deterministico(pick, detail)
        except Exception:
            recalculado = None
        item["resultado_recalculado"] = recalculado

        if estado == "PENDIENTE":
            if state == "in":
                item["va_ganando"] = (
                    f" provisional: {recalculado}" if recalculado else " mercado no decidible con marcador"
                )
                en_vivo.append(item)
            elif state == "post" and recalculado:
                sospechosos.append({
                    **item,
                    "nota": "partido TERMINADO pero el pick sigue PENDIENTE (resolver no lo proceso)",
                })
        elif recalculado and recalculado != estado:
            item["nota"] = f"guardado como {estado} pero el marcador real indica {recalculado}"
            if corregir and recalculado in ("ACIERTO", "FALLO"):
                if state == "post":
                    db.update_pick_result(pick["id"], recalculado)
                    item["corregido"] = True
                elif state == "in":
                    # En vivo con resultado guardado equivocado: reiniciar a
                    # PENDIENTE para que el resolver lo procese al terminar.
                    db.update_pick_result(pick["id"], "PENDIENTE")
                    item["reiniciado"] = True
            sospechosos.append(item)

    return {"revisados": revisados, "sospechosos": sospechosos, "en_vivo": en_vivo}


def resumen_dashboard(username: str) -> dict:
    """Bienvenida + stats del dashboard.

    - pronosticos_del_dia: SOLO los pendientes de hoy (los acertados se van
      moviendo a la seccion de acertados; los fallados nunca se muestran).
      Rango publicable 1.20-2.50; GOLDEN PICK (1.35-1.40, doble verificados)
      son 1-2 destacados, no todos.
    - acertados: picks de HOY y de AYER con resultado ACIERTO (los de ayer solo
      hasta las 23:00 Nicaragua). Cada pick trae fechaLabel ('Hoy HH:MM' /
      'Ayer HH:MM') en hora Nicaragua.
    - efectividad_hoy: aciertos / resueltos de HOY (coherente con los KPIs).
    - historico: acumulado de todos los dias (se muestra aparte). Se limpia
      automaticamente cuando se borran todos los picks (/dashboard/reset).
    """
    picks = db.list_picks_hoy() or []
    aciertos_hoy = [p for p in picks if p.get("result") == "ACIERTO"]
    fallados = [p for p in picks if p.get("result") == "FALLO"]
    # Pendientes VIGENTES: se incluyen los creados ayer para partidos de hoy o
    # manana (ej. la IA genero picks a las 9 PM para el futbol del dia
    # siguiente). Se filtran por evento vigente + calidad, no por pick_date.
    pendientes = [
        p for p in (db.list_picks_pendientes() or [])
        if p.get("result") == "PENDIENTE"
        and _pick_calidad_ok(p)
        and _evento_vigente(p)
    ]

    # Separar por jornada local de Nicaragua. El scheduler genera en una
    # ventana de 32 horas, por lo que un pick creado hoy puede ser de manana;
    # no debe aparecer mezclado con los partidos de hoy.
    ahora_nic = _hora_nicaragua()
    manana = ahora_nic.date() + timedelta(days=1)

    def _es_manana(pick):
        fecha = pick.get("eventDate")
        if not fecha:
            return False
        try:
            inicio = datetime.fromisoformat(str(fecha).replace("Z", "+00:00"))
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=timezone.utc)
            return inicio.astimezone(TZ_NICARAGUA).date() == manana
        except (ValueError, TypeError):
            return False

    pronosticos_hoy = [p for p in pendientes if not _es_manana(p)]
    pronosticos_manana = [p for p in pendientes if _es_manana(p)]

    # GOLDEN PICK primero (el frontend tambien reordena). El flag "golden"
    # se calcula ANTES del freemium: sobrevive al enmascarado, asi el orden
    # tambien es correcto para invitados (que reciben el tier en null).
    for p in pendientes:
        p["golden"] = (p.get("tier") or "") == TIER_GOLDEN
    pendientes.sort(key=lambda p: 0 if p.get("golden") else 1)

    # Aciertos visibles: hoy + ayer (hasta 23:00 Nicaragua), sin cuotas bajas
    aciertos_visibles_lista = aciertos_visibles()
    ids_hoy = {p["id"] for p in aciertos_hoy}
    aciertos_de_ayer = [p for p in aciertos_visibles_lista if p["id"] not in ids_hoy]

    historial = db.count_aciertos_historico() or {}

    resueltos_hoy = len(aciertos_hoy) + len(fallados)
    efectividad_hoy = (
        round(len(aciertos_hoy) / resueltos_hoy * 100) if resueltos_hoy else None
    )

    por_deporte = {}
    for p in pendientes:
        key = p.get("sport_label") or p.get("sport")
        por_deporte[key] = por_deporte.get(key, 0) + 1

    return {
        "welcome": f"Bienvenido, {username}",
        "stats": {
            "pronosticos_del_dia": len(pronosticos_hoy),
            "pronosticos_manana": len(pronosticos_manana),
            "pronosticos_acertados_por_la_ia": len(aciertos_visibles_lista),
            "aciertos_ayer": len(aciertos_de_ayer),
            "fallados_hoy": len(fallados),
            "resueltos_hoy": resueltos_hoy,
            "efectividad_hoy": efectividad_hoy,
            "por_deporte": por_deporte,
            "historico_aciertos": historial.get("aciertos", 0),
            "historico_resueltos": historial.get("resueltos", 0),
        },
        "pronosticos_del_dia": pronosticos_hoy,
        "pronosticos_manana": pronosticos_manana,
        "pronosticos_acertados": aciertos_visibles_lista,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# SCHEDULER AUTOMATICO
# ============================================================

INTERVALO_SEGUNDOS = max(300, int(os.getenv("DASHBOARD_PICKS_INTERVAL", "1800")))
_generando = threading.Lock()


def _ciclo():
    with _generando:
        _salud["ultimo_ciclo"] = datetime.now(timezone.utc).isoformat()
        try:
            reparados = backfill_picks_metadata()
            if reparados:
                print(f"[Dashboard] Picks reparados con equipos/logos: {reparados}", flush=True)
        except Exception:
            print("[Dashboard] Error en backfill de metadata:\n" + traceback.format_exc(), flush=True)
        try:
            stats = generar_picks_dia()
            _salud["generaciones_fallidas"] = 0
            if stats.get("generados", 0) > 0:
                _salud["ultima_generacion_ts"] = time.time()
                _salud["ultima_generacion_con_picks"] = datetime.now(timezone.utc).isoformat()
            print(f"[Dashboard] Picks automaticos: {stats}", flush=True)
        except Exception:
            _salud["generaciones_fallidas"] = _salud.get("generaciones_fallidas", 0) + 1
            if _salud["generaciones_fallidas"] >= 5:
                print(
                    f"[Dashboard] ADVERTENCIA (idea 20): {_salud['generaciones_fallidas']} "
                    f"ciclos consecutivos fallidos generando picks",
                    flush=True,
                )
            print("[Dashboard] Error en ciclo de picks:\n" + traceback.format_exc(), flush=True)
        try:
            res = resolver_picks_finalizados()
            if res.get("resueltos"):
                print(f"[Dashboard] Picks resueltos: {res}", flush=True)
        except Exception:
            print("[Dashboard] Error resolviendo picks:\n" + traceback.format_exc(), flush=True)
        _vigilante()
        _archivo_diario()


# Idea 18: si hace 3h+ que no se genera ningun pick, fuerza un ciclo
_UMBRAL_VIGILANTE_HORAS = 3


def _vigilante():
    """Vigilante: fuerza el analisis si el scheduler lleva horas sin generar."""
    if _hora_nicaragua().hour < HORA_INICIO_ANALISIS:
        return  # respeta la ventana de analisis (9 PM Nicaragua)
    ahora_ts = time.time()
    ultimo = max(
        _salud.get("ultima_generacion_ts", 0.0),
        _salud.get("ultimo_forzado_ts", 0.0),
    )
    if ahora_ts - ultimo < _UMBRAL_VIGILANTE_HORAS * 3600:
        return
    _salud["ultimo_forzado_ts"] = ahora_ts
    print("[Dashboard] VIGILANTE: sin picks nuevos hace 3h+; forzando analisis", flush=True)
    try:
        stats = generar_picks_dia(forzar=True)
        if stats.get("generados", 0) > 0:
            _salud["ultima_generacion_ts"] = time.time()
            _salud["ultima_generacion_con_picks"] = datetime.now(timezone.utc).isoformat()
        print(f"[Dashboard] VIGILANTE resultado: {stats}", flush=True)
    except Exception:
        print("[Dashboard] VIGILANTE fallo:\n" + traceback.format_exc(), flush=True)


def _archivo_diario():
    """Idea 19: una vez al dia archiva los picks de hace mas de 30 dias."""
    hoy = _hora_nicaragua().date().isoformat()
    if _salud.get("archivo_ultimo_dia") == hoy:
        return
    try:
        movidos = db.archivar_picks_antiguos(30)
        _salud["archivo_ultimo_dia"] = hoy
        if movidos:
            print(f"[Dashboard] Archivados {movidos} picks con mas de 30 dias", flush=True)
    except Exception:
        print("[Dashboard] Error archivando picks:\n" + traceback.format_exc(), flush=True)


def salud_scheduler() -> dict:
    """Idea 20: estado del scheduler para monitoreo (endpoint /dashboard/salud)."""
    s = dict(_salud)
    # Si el hilo de generacion esta vivo. Cuando es False, NADA se esta
    # generando sin importar la cuota ni los filtros (caso tipico de
    # "la IA no analiza": el hilo murio o nunca arranco).
    try:
        s["scheduler_hilo_vivo"] = scheduler_activo()
    except Exception:
        s["scheduler_hilo_vivo"] = None
    s["scheduler_intervalo_min"] = round(INTERVALO_SEGUNDOS / 60, 1)
    s["ventana_analisis"] = (
        f"desde {HORA_INICIO_ANALISIS}:00 Nicaragua, "
        f"partidos dentro de {VENTANA_ANALISIS_H}h"
    )
    s["ligas_prioritarias"] = list(LIGAS_TOP_EUROPA)
    s["verificaciones_por_acierto"] = VERIFICACIONES_ACIERTO
    # Consumo de cuota de la IA: lo primero que hay que mirar cuando el
    # dashboard deja de generar picks.
    try:
        from ai.dashboard_ia import estado_consumo, MODELOS_EN_PAUSA

        s["ia_consumo"] = estado_consumo()
        s["ia_modelos_en_pausa"] = [
            m for m, hasta in MODELOS_EN_PAUSA.items() if time.time() < hasta
        ]
    except Exception:
        pass
    if _salud.get("ultima_generacion_ts"):
        s["ultima_generacion_hace_min"] = round(
            (time.time() - _salud["ultima_generacion_ts"]) / 60, 1
        )
    try:
        s["pendientes"] = len(db.list_picks_pendientes() or [])
    except Exception:
        s["pendientes"] = None
    # Avance de la jornada en las 5 grandes ligas: cuantos partidos de la
    # ventana ya tienen pick (la IA los analiza primero). Se cachea 2 min:
    # _partidos_hoy() consulta varios scoreboards y este endpoint es de
    # monitoreo (debe responder rapido aunque el cache este frio).
    global _avance_cache
    ahora = time.time()
    if _avance_cache and ahora - _avance_cache[0] < 120:
        s.update(_avance_cache[1])
    else:
        try:
            elegibles = _partidos_hoy()
            con_pick_ids = db.eventos_con_pick()
            avance = {
                "partidos_en_ventana": len(elegibles),
                "partidos_con_pick": sum(
                    1 for p in elegibles if p["event_id"] in con_pick_ids
                ),
                "partidos_5_grandes": sum(
                    1 for p in elegibles
                    if (p.get("league") or "").lower() in LIGAS_TOP_EUROPA
                ),
            }
            _avance_cache = (ahora, avance)
            s.update(avance)
        except Exception:
            pass
    # Integraciones: solo booleanos (configurada o no), nunca el valor de la
    # variable, para saber desde produccion que falta sin filtrar secretos.
    try:
        import os as _os
        s["integraciones"] = {
            # odds-api.io es la fuente SECUNDARIA (la principal es Doradobet).
            "doradobet": True,
            "odds_api_secundaria": bool(ODDS_API_KEY),
            "groq": bool(_os.getenv("GROQ_API_KEY")),
            "you_api": bool(_os.getenv("YOU_API_KEY")),
            "pagadito": bool(
                _os.getenv("PAGADITO_UID") and _os.getenv("PAGADITO_WSK")
            ),
            "db": bool(
                _os.getenv("MYSQL_URL") or _os.getenv("DATABASE_URL")
                or _os.getenv("MYSQL_HOST")
            ),
            "ai36": bool(_os.getenv("AI36_GROQ_API_KEY")),
        }
    except Exception:
        pass
    # Descartes recientes: partidos ya analizados que no pasaron filtros
    # (cuota baja, sin datos, mercado prohibido) -> llamadas de IA ahorradas.
    try:
        motivos = db.descartes_recientes(horas=6)
        conteo: dict = {}
        for motivo in motivos.values():
            clave = motivo or "?"
            conteo[clave] = conteo.get(clave, 0) + 1
        s["descartes_6h"] = conteo
        s["descartes_6h_total"] = len(motivos)
    except Exception:
        pass
    return s


def _loop():
    time.sleep(20)  # dejar arrancar la app primero
    while True:
        try:
            _ciclo()
        except Exception:
            # Un fallo inesperado NO debe matar el hilo: si muere, deja de
            # haber picks hasta el proximo reinicio de Northflank y nadie se
            # entera (un thread daemon muere en silencio). Se loguea y el
            # siguiente ciclo reintenta a los INTERVALO_SEGUNDOS.
            print("[Dashboard] Error en ciclo (el hilo sigue vivo):\n"
                  + traceback.format_exc(), flush=True)
        time.sleep(INTERVALO_SEGUNDOS)


# Hilo vivo del scheduler (visibilizable en /dashboard/salud)
_hilo_scheduler = None
_scheduler_lock = threading.Lock()


def iniciar_scheduler():
    """Arranca el hilo daemon que genera y resuelve picks automaticos.

    Idempotente: si el hilo ya corre no crea otro. Un watchdog lo vigila y
    lo reinicia si muriera (defensa en profundidad del try/except de _loop).
    """
    global _hilo_scheduler
    with _scheduler_lock:
        if _hilo_scheduler is not None and _hilo_scheduler.is_alive():
            print("[Dashboard] Scheduler ya corriendo; no se duplica", flush=True)
            return
        _hilo_scheduler = threading.Thread(
            target=_loop, daemon=True, name="dashboard-picks"
        )
        _hilo_scheduler.start()
        print("[Dashboard] Scheduler de picks automaticos iniciado", flush=True)
    threading.Thread(
        target=_watchdog_scheduler, daemon=True, name="dashboard-watchdog"
    ).start()


def _watchdog_scheduler():
    """Revisa cada 5 min que el hilo del scheduler siga vivo; lo reinicia."""
    while True:
        time.sleep(300)
        with _scheduler_lock:
            vivo = _hilo_scheduler is not None and _hilo_scheduler.is_alive()
        if not vivo:
            print("[Dashboard] WATCHDOG: hilo del scheduler caido; reiniciando",
                  flush=True)
            iniciar_scheduler()
            return  # el watchdog que cree el nuevo arranque se encarga


def scheduler_activo() -> bool:
    """True si el hilo de generacion de picks esta vivo."""
    with _scheduler_lock:
        return _hilo_scheduler is not None and _hilo_scheduler.is_alive()
