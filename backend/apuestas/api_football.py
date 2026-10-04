"""
Segunda fuente de estadisticas de partido: API-Football (api-sports.io).
============================================================

Por que existe: FotMob cubre el 75% de los partidos (medido sobre 96 partidos
de 13 ligas). En el 25% restante NO hay corners, tarjetas ni faltas, y eso
significa que el mercado no se puede evaluar: hoy se descarta sin apostar. Con
esta fuente el hueco se cubre.

Como encaja: implementa LA MISMA INTERFAZ que backend/apuestas/fotmob_stats.py
(total_estadistica, breakdown_equipo, partido_por_equipos...), asi que se
encadena sin tocar a quien la usa:

    FotMob  ->  API-Football  ->  (nada: el pick no se publica)

La ultima vez que FotMob falla, esta fuente decide. Si tampoco, el motor
devuelve SIN_DATOS y NO se inventa nada.

LIMITE DE CUOTA (importante):
    El plan gratis de api-sports.io son 100 requests/dia. El generador pide
    stats de ~76 partidos al dia, asi que llamar SIEMPRE en paralelo a FotMob
    reventaria la cuota al segundo dia. Por eso esta fuente esta DISABLEDA por
    defecto y solo se usa como RESPALDO: primero FotMob y, solo si devuelve
    None, se llama aqui. En operacion normal son ~15-20 requests/dia.

CONFIGURACION:
    API_FOOTBALL_KEY=xxxx   en el .env  (registro gratis en
                             https://www.api-football.com/)
    Sin la key el modulo no hace ninguna llamada y devuelve None siempre.
"""

import os
import threading
import time

import requests

BASE = "https://v3.football.api-sports.io"
H_BASE = {"User-Agent": "3SIXTYBETS"}
_cache = {}
_lock = threading.Lock()
TTL = 86400  # el resultado de un partido ya terminado no cambia nunca

# Mapeo de las stats que devuelve api-sports a las del motor.
# La API devuelve objetos {"type": "Corners", "value": 7}.
_STATS = {
    "corners": "Corners",
    "yellow_cards": "Yellow Cards",
    "red_cards": "Red Cards",
    "fouls": "Fouls Played",
    "shots": "Shots Total",
    "shots_on_target": "Shots on Goal",
    "offsides": "Offsides",
}

# Estados finales de partido en api-sports.
_FINAL = ("FT", "AET", "PEN", "FT-PEN", "After Penalties")


def clave() -> str:
    """API key desde el entorno. '' si no esta configurada."""
    return (os.getenv("API_FOOTBALL_KEY") or "").strip()


def disponible() -> bool:
    """True si hay key. Sin key el modulo es un no-op."""
    return bool(clave())


def _get(ruta, params=None):
    """GET cacheado. None si no hay key, si da error o si se acabo la cuota."""
    k = clave()
    if not k:
        return None

    params = dict(params or {})
    params["key"] = k
    firma = ruta + "?" + "&".join(f"{a}={b}" for a, b in sorted(params.items()))
    ahora = time.time()
    with _lock:
        hit = _cache.get(firma)
        if hit and ahora - hit[0] < TTL:
            return hit[1]

    try:
        r = requests.get(f"{BASE}{ruta}", headers=H_BASE, params=params, timeout=20)
        if r.status_code != 200:
            # 429 = cuota agotada: se cachea como None hasta mañana para no
            # reintentar en cada ciclo (seguir reintentando la quema del todo).
            if r.status_code == 429:
                with _lock:
                    _cache[firma] = (ahora + TTL, None)
            return None
        j = r.json() or {}
        # La API devuelve 200 con 'errors' dentro cuando la cuota se agota.
        if j.get("errors"):
            return None
        datos = j.get("response")
    except Exception:
        datos = None

    with _lock:
        _cache[firma] = (ahora, datos)
    return datos


# === Partidos =============================================================

def _stats_fixture(fixture):
    """{metrica_motor: [local, visitante]} leyendo fixture.statistics."""
    st = ((fixture or {}).get("statistics") or {}) or {}
    salida = {}
    for motor, tipo in _STATS.items():
        teams = st.get("teams") or []
        for t in teams:
            val = None
            for s in t.get("statistics") or []:
                if s.get("type") == tipo:
                    val = s.get("value")
                    break
            if val is not None:
                try:
                    salida.setdefault(motor, []).append(int(val))
                except (TypeError, ValueError):
                    pass
        if motor in salida and len(salida[motor]) != 2:
            del salida[motor]  # solo viene un lado: no sirve para el total
    return salida


def partido_por_equipos(home_name, away_name, fecha=None):
    """Misma firma que fotmob_stats. dict con marcador, o None."""
    from backend.apuestas.fotmob_stats import _clave_texto

    if not home_name or not away_name or not disponible():
        return None
    h, a = _clave_texto(home_name), _clave_texto(away_name)

    dias = []
    if fecha:
        try:
            from datetime import date, timedelta
            base = date.fromisoformat(str(fecha)[:10])
            dias = [(base + timedelta(days=o)).isoformat() for o in (-2, -1, 0, 1, 2)]
        except ValueError:
            dias = []
    if not dias:
        from datetime import date
        dias = [date.today().isoformat()]

    mejor = None
    for d in dias:
        for fx in (_get("/fixtures", {"date": d}) or []):
            th = ((fx.get("teams") or {}).get("home") or {}).get("name") or ""
            ta = ((fx.get("teams") or {}).get("away") or {}).get("name") or ""
            if (_clave_texto(th) == h and _clave_texto(ta) == a) or \
               (_clave_texto(th) == a and _clave_texto(ta) == h):
                fecha_fx = (fx.get("fixture") or {}).get("date") or ""
                if mejor is None or fecha_fx > ((mejor.get("fixture") or {}).get("date") or ""):
                    mejor = fx
    if not mejor:
        return None

    f = mejor.get("fixture") or {}
    th = (mejor.get("teams") or {}).get("home") or {}
    ta = (mejor.get("teams") or {}).get("away") or {}
    status = mejor.get("status") or {}

    def _int(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    return {
        "id": f.get("id"),
        "home_name": th.get("name"),
        "away_name": ta.get("name"),
        "home_score": _int(th.get("goals")),
        "away_score": _int(ta.get("goals")),
        "finished": (status.get("short") or "") in _FINAL,
        "utc": f.get("date"),
    }


def total_estadistica(match_id, metrica, periodo="All"):
    """Total (local+visitante) de una metrica. int o None.

    api-sports no desglosa por periodos, asi que 'FirstHalf' devuelve None: es
    preferible no responder que responder un total y fazerlo pasar por primera
    mitad.
    """
    if periodo != "All" or not disponible():
        return None
    datos = _get("/fixtures", {"id": match_id})
    if not datos:
        return None
    v = _stats_fixture(datos[0]).get(metrica)
    if not v or len(v) != 2:
        return None
    return v[0] + v[1]


def breakdown_equipo(match_id, metrica, home_name=None, away_name=None,
                     periodo="All"):
    """(local, visitante) de una metrica. (None, None) si no hay."""
    if periodo != "All" or not disponible():
        return None, None
    datos = _get("/fixtures", {"id": match_id})
    if not datos:
        return None, None
    v = _stats_fixture(datos[0]).get(metrica)
    if not v or len(v) != 2:
        return None, None
    return v[0], v[1]


def total_estadistica_de_pick(pick, metrica, periodo="All"):
    """Total de una metrica para el partido de un pick. int o None."""
    info = partido_por_equipos(pick.get("home_name"), pick.get("away_name"),
                               pick.get("eventDate"))
    if not info or not info.get("id"):
        return None
    return total_estadistica(info["id"], metrica, periodo)
