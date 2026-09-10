"""
Sportradar (trial) - fuente alternativa de cuotas y datos para 3SIXTYBETS.

Servicios del trial:
- Soccer API v4 (Base + Extended Base): calendarios, resumenes, estadisticas.
- Odds Comparison Regular v2: cuotas reales de multiples bookmakers.
- NBA API: calendarios y partidos.

Limites del trial: 1.000 peticiones por servicio y 1 QPS (el cliente hace
throttle automatico). La key vive en la variable SPORTRADAR_API_KEY.
Si la API responde 403 Authentication Error, la key es invalida o el trial
no esta activo: el cliente hace backoff 15 min y el sistema sigue con
ESPN + odds-api.io sin interrupcion.
"""

import os
import time

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

API_KEY = os.getenv("SPORTRADAR_API_KEY", "")
# Algunos servicios (ej. Odds Comparison) usan key propia si se define
ODDS_API_KEY = os.getenv("SPORTRADAR_ODDS_API_KEY", "") or API_KEY
NBA_API_KEY = os.getenv("SPORTRADAR_NBA_API_KEY", "") or API_KEY
BASES = ["https://api.sportradar.com", "https://api.sportradar.us"]
QPS_THROTTLE = 1.1  # segundos minimos entre llamadas (plan 1 QPS)
KEY_BACKOFF = 900  # 15 min sin llamar cuando la key da 403

_last_call = 0.0
_key_rechazada_hasta = 0.0
_cache = {}  # ruta -> (timestamp, data) con TTL por entrada
_RUTAS_ODDS = [
    "oddscomparison-regular/trial/v2/en/sports/{sport_path}/events/schedule.json",
    "oddscomparison/trial/v2/en/sports/{sport_path}/events/schedule.json",
]
_TORNEOS_FUTBOL = (
    "uefa.champions_league", "uefa.europa_league",
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
    "mex.1", "usa.mls", "bra.1", "arg.1", "ned.1", "por.1",
)


def _throttle():
    global _last_call
    espera = QPS_THROTTLE - (time.time() - _last_call)
    if espera > 0:
        time.sleep(espera)
    _last_call = time.time()


def get(path: str, ttl: int = 900, api_key: str | None = None):
    """GET contra Sportradar con cache y throttle. None si falla."""
    global _key_rechazada_hasta
    api_key = api_key or API_KEY
    if not api_key:
        return None
    if time.time() < _key_rechazada_hasta:
        return None  # key rechazada hace poco: no insistir
    ahora = time.time()
    cached = _cache.get(path)
    if cached and ahora - cached[0] < ttl:
        return cached[1]

    _throttle()
    for base in BASES:
        try:
            r = requests.get(
                f"{base}/{path}",
                headers={"x-api-key": api_key, "accept": "application/json"},
                timeout=20,
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = None
                _cache[path] = (time.time(), data)
                return data
            if r.status_code == 403:
                # Key rechazada: backoff y no insistir con el otro host
                _key_rechazada_hasta = time.time() + KEY_BACKOFF
                _cache[path] = (time.time(), None)
                return None
        except Exception:
            continue
    _cache[path] = (time.time(), None)
    return None


def soccer_schedule(fecha: str):
    """Calendario de futbol del dia (fecha 'YYYY-MM-DD')."""
    return get(f"soccer/trial/v4/en/schedules/{fecha}/schedule.json")


def soccer_summary(event_id: str):
    """Resumen de un partido: marcador, estadisticas, alineaciones."""
    return get(f"soccer/trial/v4/en/sport_events/{event_id}/summary.json")


def nba_schedule(fecha: str):
    """Calendario NBA del dia (fecha 'YYYY/MM/DD').

    Ruta correcta del trial v8: /games/{Y}/{M}/{D}/schedule.json
    Devuelve {'date', 'league', 'games': [{'home', 'away', 'status'...}]}
    """
    for version in ("v8", "v7"):
        data = get(
            f"nba/trial/{version}/en/games/{fecha}/schedule.json",
            api_key=NBA_API_KEY,
        )
        if data:
            return data
    return None


# ============================================================
# ODDS COMPARISON v2 (cuotas reales de multiples bookmakers)
# ============================================================

def _norm(nombre: str) -> str:
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


def odds_eventos_torneo(sport_path: str):
    """Eventos con cuotas de un torneo (sport_path ej 'soccer/eng.1')."""
    for plantilla in _RUTAS_ODDS:
        data = get(plantilla.format(sport_path=sport_path), ttl=1800, api_key=ODDS_API_KEY)
        if data:
            return data
    return None


def cuotas_partido(sport: str, home_name: str, away_name: str, fecha: str | None = None):
    """Cuotas reales del partido desde Odds Comparison (Sportradar).

    Busca el partido por nombres de equipos entre los torneos candidatos.
    Devuelve (evento, mercados) o (None, None). Solo soccer por ahora.
    """
    if sport != "soccer":
        return None, None

    nh, na = _norm(home_name), _norm(away_name)
    evento = None
    torneo_encontrado = None
    for torneo in _TORNEOS_FUTBOL:
        data = odds_eventos_torneo(f"soccer/{torneo}")
        if not data:
            continue
        for ev in data.get("events") or []:
            comp = ev.get("competitors") or []
            local = (comp[0] or {}).get("name") if len(comp) > 0 else None
            visita = (comp[1] or {}).get("name") if len(comp) > 1 else None
            if not local or not visita:
                continue
            nl, nv = _norm(local), _norm(visita)
            ok = (
                (nh in nl or nl in nh) and (na in nv or nv in na)
            ) or (
                (nh in nv or nv in nh) and (na in nl or nl in na)
            )
            if ok:
                if fecha and str(ev.get("scheduled") or "")[:10] != fecha:
                    continue
                evento, torneo_encontrado = ev, torneo
                break
        if evento:
            break

    if not evento:
        return None, None

    mercados = None
    for plantilla in (
        "oddscomparison-regular/trial/v2/en/sports/{torneo}/events/{eid}/markets.json",
        "oddscomparison/trial/v2/en/sports/{torneo}/events/{eid}/markets.json",
    ):
        data = get(plantilla.format(torneo=torneo_encontrado, eid=evento.get("id")), ttl=600, api_key=ODDS_API_KEY)
        if data and data.get("markets"):
            mercados = data["markets"]
            break
    return evento, mercados


def formato_cuotas(evento: dict, mercados: list, max_mercados: int = 20) -> str:
    """Texto de cuotas en el mismo estilo que odds-api.io para el prompt."""
    comp = evento.get("competitors") or []
    local = (comp[0] or {}).get("name", "?") if comp else "?"
    visita = (comp[1] or {}).get("name", "?") if len(comp) > 1 else "?"
    lineas = [f"CUOTAS REALES ({local} vs {visita}, Sportradar odds comparison):"]
    for m in (mercados or [])[:max_mercados]:
        nombre = m.get("name", "?")
        for o in (m.get("odds") or [])[:2]:
            pares = [f"{k}={v}" for k, v in o.items() if v not in (None, "")]
            lineas.append(f"- {nombre}: {', '.join(pares)}")
    lineas.append(
        "USA estas cuotas reales para calcular valor; la linea que elijas debe "
        "respetar los minimos del catalogo."
    )
    return "\n".join(lineas)

