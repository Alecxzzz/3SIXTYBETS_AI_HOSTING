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
# Los acertados del dia anterior se muestran solo hasta las 23:00 Nicaragua
HORA_CORTE_ACERTADOS = 20
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
# ============================================================

ODDS_API_KEY = os.getenv("ODDS_API_KEY") or os.getenv("AI36_ODDS_API_KEY") or ""
if not ODDS_API_KEY:
    # Sin key, el sitio sigue funcionando: las cuotas caen a ESPN/estimadas.
    # Configurar ODDS_API_KEY en las variables de entorno del hosting.
    print("[WARN] ODDS_API_KEY no configurada: cuotas reales de odds-api.io "
          "deshabilitadas (se usan cuotas de ESPN/estimadas).", flush=True)
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
    if _odds_bloqueado():
        return None
    try:
        import requests

        params = {"apiKey": ODDS_API_KEY, **params}
        r = requests.get(f"{ODDS_API_BASE}{path}", params=params, timeout=20)
        if r.status_code != 200:
            print(f"[Dashboard] odds-api.io {path} -> {r.status_code}: {r.text[:150]}")
            # 429 (limite diario/horario): pausar 15 min para no quemar cuota
            if r.status_code == 429:
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
        from cuotas_doradobet import get_events_deporte, _encontrar_evento, detalle_mercado_para_ia

        data = get_events_deporte(sport)
        if data:
            evento = _encontrar_evento(data, home_name, away_name, None)
            if evento:
                detalle = detalle_mercado_para_ia(evento.get("id"))
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
}

DEPORTES_DASHBOARD = list(MERCADOS_POR_DEPORTE.keys())

# ============================================================
# PROMPT MAESTRO (usado por LAS DOS IAS: 365AI y Demian)
# ============================================================


def catalogo_texto():
    lineas = []
    for sport, (label, mercados) in MERCADOS_POR_DEPORTE.items():
        lineas.append(f"{label.upper()}:")
        for m in mercados:
            lineas.append(f"- {m}")
        lineas.append("")
    return "\n".join(lineas)


PROMPT_PICKS = """Eres 3SIXTYBETS AI, analista cuantitativo de apuestas deportivas.

SOLO PUEDES USAR ESTOS PICKS DEFINIDOS PARA CADA DEPORTE:

""" + catalogo_texto() + """
REGLAS OBLIGATORIAS:
1. USA SOLO los mercados listados arriba. NUNCA inventes mercados.
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
7. Responde EXCLUSIVAMENTE con un JSON valido, sin texto extra, con esta forma exacta:
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


# ============================================================
# GENERACION DE PICKS
# ============================================================


def _partidos_hoy():
    """Partidos elegibles para analisis: en vivo + los que arrancan pronto.

    Ventana: los que inician dentro de las proximas VENTANA_ANALISIS_H horas
    (y los EN VIVO siempre). A las 21:00 Nicaragua los partidos de las grandes
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
                    "league": g.get("league_code"),
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


def _preguntar_ia(mensaje: str):
    """Consulta temporal a Demian para generar picks del Dashboard.

    Demian se usa aquí de forma explícita mientras no exista una fuente de
    cuotas体育 verificada para todas las competiciones. No modifica el chat.
    """
    try:
        from ai.model import generar_respuesta_you

        contenido = generar_respuesta_you(PROMPT_PICKS, mensaje)
        if contenido and not str(contenido).startswith(("ERROR:", "Error leyendo")):
            return contenido, "Demian tipster"
    except Exception as exc:
        print(f"[Dashboard] Demian fallo: {exc}")
    return None, None


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
            texto, _modelo = _preguntar_ia(pregunta)
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

    # Partidos ya analizados y descartados hace poco (cuota baja, sin datos,
    # mercado prohibido...): no se vuelven a gastar llamadas de IA en ellos.
    descartes = db.descartes_recientes(horas=6)
    try:
        db.limpiar_descartes(horas=48)
    except Exception:
        pass

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
            if p["sport"] == "soccer":
                # FUTBOL: mas volumen. Si ya se usaron todos los mercados del
                # catalogo hoy, se permite reutilizarlos para partidos nuevos
                # (cada partido lleva SU pick aunque el mercado ya salio hoy).
                disponibles = list(mercados)
            else:
                break  # otros deportes: se agotaron los mercados del catalogo

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
                    "Cuotas REALES de ESPN: "
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

        texto, modelo = _preguntar_ia(mensaje)
        pick = _parsear_pick_json(texto)

        if not pick:
            # {"error": "sin datos"} es una respuesta valida de la IA: ese
            # partido no tiene datos, no se reintenta cada ciclo. Un fallo
            # transitorio (respuesta vacia/cortada) SI se reintenta.
            if '"error"' in (texto or "").lower():
                db.registrar_descarte(p["event_id"], "sin_datos")
            else:
                errores += 1
            continue

        market = (pick.get("market") or "").strip()

        # Mercados prohibidos: "sin empate" (DNB) y similares nunca se muestran.
        if _mercado_prohibido(
            market, str(pick.get("titulo") or ""), str(pick.get("selection") or "")
        ):
            rechazados_prohibido += 1
            db.registrar_descarte(p["event_id"], "mercado_prohibido")
            continue

        # Validar que el mercado pertenece al catalogo del deporte y no repite
        # (el no-repetir solo bloquea en deportes que NO son futbol: en futbol
        # se genera pick para cada partido con mas volumen)
        if _market_norm(market) not in {_market_norm(m) for m in disponibles}:
            # Los mercados de props adicionales (Goleador, Asistencias,
            # Remates a Puerta, Tarjetas, etc.) pueden existir solo en
            # GetEventDetails; se aceptan únicamente si Doradobet los publicó.
            from cuotas_doradobet import event_id_doradobet, detalle_mercado_para_ia
            evento_dorado = event_id_doradobet(p["sport"], p["home_name"], p["away_name"])
            detalle = detalle_mercado_para_ia(evento_dorado) if evento_dorado else ""
            if not detalle or _market_norm(market) not in _market_norm(detalle):
                errores += 1
                continue
        if p["sport"] != "soccer" and _market_norm(market) in mercados_usados:
            errores += 1
            continue

        # RANGO GENERAL: la cuota IA debe estar en 1.20-2.50 (los GOLDEN
        # 1.35-1.40 son 1-2 destacados, no todos).
        try:
            odds_pick = float(pick.get("odds") or 0)
        except (TypeError, ValueError):
            odds_pick = 0
        if not odds_pick or not (ODDS_MINIMA <= odds_pick <= ODDS_MAXIMA):
            rechazados_cuota += 1
            db.registrar_descarte(p["event_id"], "cuota_fuera_rango")
            continue

        # Gate de calidad: nada de picks genericos o sin datos reales
        # (equipos '?', 'Jugador A', rationale 'sin datos...', sin cuota).
        rationale_txt = str(pick.get("rationale") or "") + " " + str(pick.get("titulo") or "")
        if (
            not _nombre_valido(p["home_name"])
            or not _nombre_valido(p["away_name"])
            or _texto_sin_datos(rationale_txt)
            or _texto_sin_datos(p["event_name"])
        ):
            rechazados_calidad += 1
            db.registrar_descarte(p["event_id"], "calidad")
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
        es_golden = _es_golden(odds_final)
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
        "meta_min_juegos": MIN_JUEGOS_MANANA,
        "omitidos_ya_con_pick": omitidos,
        "omitidos_descartados": omitidos_descartados,
        "errores": errores,
        "rechazados_cuota": rechazados_cuota,
        "rechazados_calidad": rechazados_calidad,
        "rechazados_prohibido": rechazados_prohibido,
        "rechazados_sin_verificar": rechazados_sin_verificar,
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

    match = re.search(r"(over|under)\s*\+?\s*([0-9]+(?:\.[0-9]+)?)", texto)
    if not match:
        return None
    es_over = match.group(1) == "over"
    linea = float(match.group(2))

    jugador = str(pick.get("titulo") or "").split(":")[0].strip()
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
    """Resuelve over/under de TARJETAS y CORNERS de futbol con Sofascore.

    El marcador global no determina estos mercados (antes la IA adivinaba);
    aqui se usa la estadistica real del partido (periodo ALL).
    """
    if pick.get("sport") != "soccer":
        return None
    market = (pick.get("market") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    texto = f"{market} {titulo} {(pick.get('selection') or '').lower()}"

    if "tarjeta" in texto or "card" in texto:
        claves = ("yellowcards", "redcards")
    elif "corner" in texto:
        claves = ("corners",)
    else:
        return None

    match = re.search(r"(over|m[áa]s de|under|menos de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto)
    if not match:
        return None
    es_over = match.group(1).startswith(("over", "m"))
    try:
        linea = float(match.group(2).replace(",", "."))
    except ValueError:
        return None

    total = _sofascore_stat_total(pick, claves)
    if total is None:
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
        texto, _ = _preguntar_ia(mensaje)
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

    texto, _ = _preguntar_ia(mensaje)
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


def _detalle_resolucion(pick: dict):
    """Obtiene el detalle del partido para resolver el pick.

    A diferencia de get_game_detail (que para soccer solo busca en el
    scoreboard de hoy), usa la liga guardada en el pick para consultar el
    summary de ESPN aunque el partido sea de dias anteriores.
    """
    import sports

    sport = pick["sport"]
    eid = pick["eventId"]

    # Futbol: summary con la liga guardada
    if sport == "soccer":
        league = pick.get("league")
        if not league:
            return sports.get_game_detail(sport, eid)
        try:
            import requests as http_requests

            from sports import ESPN_BASE, _parse_number

            url = f"{ESPN_BASE}/soccer/{league}/summary?event={eid}"
            data = http_requests.get(url, timeout=20).json()
            header = data.get("header") or {}
            comps = (header.get("competitions") or [{}])[0]
            state = ((comps.get("status") or {}).get("type") or {}).get("state")
            teams = []
            for c in comps.get("competitors", []):
                teams.append({
                    "name": (c.get("team") or {}).get("displayName", "?"),
                    "score": _parse_number(c.get("score")),
                    "homeAway": c.get("homeAway"),
                })
            return {"state": state, "teams": teams}
        except Exception:
            return sports.get_game_detail(sport, eid)

    # Resto de deportes: get_game_detail funciona directo
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
        # Expirar picks antiguos que ya no se pueden resolver (sin liga,
        # evento fuera de ESPN, etc.) para no bloquear la cola de pendientes
        try:
            creado = datetime.fromisoformat(pick.get("createdAt") or "")
            horas = (datetime.now(timezone.utc).replace(tzinfo=None) - creado).total_seconds() / 3600
        except (ValueError, TypeError):
            horas = 0
        if horas > 24:
            db.update_pick_result(pick["id"], "ANULADO")
            continue

        # Expirar pendientes cuyo EVENTO ya paso hace mas de 3h y sigue sin
        # resolverse (eventos fantasma de ESPN, tenis sin datos, etc.)
        try:
            ev = datetime.fromisoformat(str(pick.get("eventDate") or "").replace("Z", "+00:00"))
            horas_evento = (datetime.now(timezone.utc) - ev).total_seconds() / 3600
        except (ValueError, TypeError):
            horas_evento = 0
        if horas_evento > 3:
            db.update_pick_result(pick["id"], "ANULADO")
            continue

        try:
            detail = _detalle_resolucion(pick)
            # Fallback: marcador final desde odds-api.io si ESPN no lo tiene
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

        # 1.5) Mercados con estadistica real disponible:
        #   - props de jugador MLB -> boxscore oficial (statsapi)
        #   - tarjetas/corners futbol -> estadisticas de Sofascore
        # El marcador global NO determina estos mercados (la IA adivinaba).
        try:
            resultado = _resolver_prop_mlb(pick) or _resolver_stats_sofascore(pick)
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
    s["ventana_analisis"] = (
        f"desde {HORA_INICIO_ANALISIS}:00 Nicaragua, "
        f"partidos dentro de {VENTANA_ANALISIS_H}h"
    )
    s["ligas_prioritarias"] = list(LIGAS_TOP_EUROPA)
    s["verificaciones_por_acierto"] = VERIFICACIONES_ACIERTO
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
            "odds_api": bool(ODDS_API_KEY),
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
        _ciclo()
        time.sleep(INTERVALO_SEGUNDOS)


def iniciar_scheduler():
    """Arranca el hilo daemon que genera y resuelve picks automaticamente."""
    hilo = threading.Thread(target=_loop, daemon=True, name="dashboard-picks")
    hilo.start()
    print("[Dashboard] Scheduler de picks automaticos iniciado", flush=True)
