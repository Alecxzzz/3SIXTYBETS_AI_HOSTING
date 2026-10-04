"""
Segunda fuente de estadisticas: ESPN (site.api.espn.com).
========================================================

Por que entra: FotMob cubre ~75% de los partidos. ESPN cubre precisamente las
ligas donde FotMob falla (col.1, arg.1, mex.1, usa.1...), que son ademas las de
mas volumen en los picks. Y no aporta ninguna dependencia nueva: sports.py y
main.py ya consumen esta API a diario.

Que trae, verificado sobre Newcastle 4-3 Bournemouth (eng.1):
    Newcastle:    faltas 19, amarillas 2, rojas 0, corners 4
    Bournemouth:  faltas 17, amarillas 3, rojas 0, corners 3
Son 28 metricas por equipo: corners, tarjetas, faltas, tiros, a puerta,
posesion, pases, cruces, entradas, despejes y fuera de juego.

AVISO IMPORTANTE (descubierto probando): boxscore.teams[].statistics SOLO
existe en partidos FINALIZADOS. En un partido en vivo el summary llega sin
estadisticas y la palabra 'corner' no aparece en NINGUN sitio del JSON. Por eso
buscar 'corner' sin comprobar el estado del partido da falsos negativos: fue
justo lo que hizo pensar que ESPN no tenia corners.

Como encaja (misma interfaz que fotmob_stats y api_football):

    FotMob  ->  ESPN  ->  API-Football  ->  nada (el pick no se publica)

ESPN va segundo porque FotMob es una sola llamada y ya va cacheado; ESPN
necesita un scoreboard + un summary por partido, asi que solo se consulta
cuando FotMob no tiene el dato. Si FotMob ya dio el numero, ESPN no se llama:
la cadena solo avanza cuando la anterior devuelve None.
"""

import threading
import time
from datetime import date, datetime, timedelta

import requests

BASE_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
_cache = {}
_lock = threading.Lock()
TTL = 86400  # el resultado de un partido ya terminado no cambia nunca

# Metricas del motor -> nombre de la stat en ESPN.
_METRICAS = {
    "corners": "wonCorners",
    "yellow_cards": "yellowCards",
    "red_cards": "redCards",
    "fouls": "foulsCommitted",
    "shots": "totalShots",
    "shots_on_target": "shotsOnTarget",
    "offsides": "offsides",
}

# slugs de ESPN常用 para las ligas que aparecen en los picks
_LIGAS_COMUNES = (
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "ned.1", "por.1",
    "col.1", "arg.1", "mex.1", "usa.1", "bra.1", "chi.1", "per.1",
    "uefa.champions", "uefa.europa", "uefa.nations",
)


def _get(url, params=None):
    firma = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    ahora = time.time()
    with _lock:
        hit = _cache.get(firma)
        if hit and ahora - hit[0] < TTL:
            return hit[1]
    try:
        r = requests.get(url, headers=H, params=params, timeout=20)
        datos = r.json() if r.status_code == 200 else None
    except Exception:
        datos = None
    with _lock:
        _cache[firma] = (ahora, datos)
    return datos


def _norm(t):
    from backend.apuestas.fotmob_stats import _clave_texto
    return _clave_texto(t)


def _eventos(liga, dia):
    d = _get(f"{BASE_SITE}/{liga}/scoreboard", {"dates": dia, "limit": "60"}) or {}
    return d.get("events") or []


def partido_por_equipos(home_name, away_name, fecha=None, league=None):
    """Mismo formato que fotmob_stats. dict o None."""
    if not home_name or not away_name:
        return None
    h, a = _norm(home_name), _norm(away_name)

    base = None
    if fecha:
        try:
            base = datetime.fromisoformat(str(fecha).replace("Z", "+00:00")).date()
        except ValueError:
            try:
                base = date.fromisoformat(str(fecha)[:10])
            except ValueError:
                base = None
    hoy = date.today()
    inicio = base or hoy
    # +1 dia por si el partido se、抓 registra con fecha UTC distinta.
    dias = [(inicio + timedelta(days=o)) for o in (0, 1, -1)]

    ligas = [league] if league else list(_LIGAS_COMUNES)
    for liga in ligas:
        if not liga:
            continue
        for dia in dias:
            for e in _eventos(liga, dia.strftime("%Y%m%d")):
                comp = e.get("competitions") or []
                if not comp:
                    continue
                c = comp[0]
                th = ((c.get("competitors") or [{}])[0] or {})
                ta = ((c.get("competitors") or [{}, {}])[1] or {})
                # el orden de competitors no esta garantizado
                pares = {th.get("homeAway"): th, ta.get("homeAway"): ta}
                local = pares.get("home") or {}
                visitante = pares.get("away") or {}
                if not local or not visitante:
                    local, visitante = th, ta
                if {_norm(local.get("team", {}).get("displayName")),
                    _norm(visitante.get("team", {}).get("displayName"))} != {h, a}:
                    continue
                st = (e.get("status") or {}).get("type") or {}
                return {
                    "id": str(e.get("id")),
                    "league": liga,
                    "home_name": local.get("team", {}).get("displayName"),
                    "away_name": visitante.get("team", {}).get("displayName"),
                    "home_score": local.get("score"),
                    "away_score": visitante.get("score"),
                    "finished": bool(st.get("completed")),
                    "utc": e.get("date"),
                }
    return None


def _stats_de_equipo(league, event_id):
    """{metrica_motor: [local, visitante]} desde boxscore del summary.

    Devuelve {} si el partido no ha terminado: ESPN solo publica
    boxscore.teams[].statistics cuando el evento esta finalizado.
    """
    if not league or not event_id:
        return {}
    d = _get(f"{BASE_SITE}/{league}/summary", {"event": event_id}) or {}
    teams = ((d.get("boxscore") or {}).get("teams") or [])
    if len(teams) < 2:
        return {}

    def _por_lado(t):
        salida = {}
        for s in (t.get("statistics") or []):
            motor = _INVERSA.get(s.get("name"))
            if motor:
                try:
                    salida[motor] = int(float(s.get("displayValue")))
                except (TypeError, ValueError):
                    pass
        return salida

    # ESPN no siempre marca homeAway en el boxscore: se deduce por orden o por
    # el nombre del equipo del header.
    header_home = ""
    try:
        comp = ((d.get("header") or {}).get("competitions") or [{}])[0] or {}
        for c in (comp.get("competitors") or []):
            if c.get("homeAway") == "home":
                header_home = _norm((c.get("team") or {}).get("displayName"))
    except Exception:
        pass

    a, b = teams[0], teams[1]
    if header_home:
        if _norm((b.get("team") or {}).get("displayName")) == header_home:
            a, b = b, a

    sa, sb = _por_lado(a), _por_lado(b)
    # sa/sb guardan ESCALARES (int), no listas: se emparejan aqui.
    return {k: [sa[k], sb[k]] for k in sa if k in sb and k in sb}


_INVERSA = {v: k for k, v in _METRICAS.items()}


def breakdown_equipo(match_id, metrica, league=None, periodo="All"):
    """(local, visitante) de una metrica. (None, None) si no hay.

    ESPN no desglosa por periodos, asi que periodo != 'All' devuelve None: es
    preferible no responder que responder un total y hacerlo pasar por primera
    mitad.
    """
    if periodo != "All" or not league:
        return None, None
    v = _stats_de_equipo(league, str(match_id)).get(metrica)
    if not v or len(v) != 2:
        return None, None
    return v[0], v[1]


def total_estadistica(match_id, metrica, league=None, periodo="All"):
    """Total (local+visitante) de una metrica. int o None."""
    v = breakdown_equipo(match_id, metrica, league, periodo)
    if not v or v[0] is None or v[1] is None:
        return None
    return v[0] + v[1]


def total_estadistica_de_pick(pick, metrica, periodo="All"):
    """Total de una metrica para el partido de un pick. int o None."""
    info = partido_por_equipos(pick.get("home_name"), pick.get("away_name"),
                              pick.get("eventDate"), pick.get("league"))
    if not info or not info.get("finished"):
        return None
    return total_estadistica(info["id"], metrica, info.get("league"), periodo)
