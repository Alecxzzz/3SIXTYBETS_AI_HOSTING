# -*- coding: utf-8 -*-
"""
Historico real de partidos de futbol via la API JSON de FotMob.

Por que esta y no footystats/sofascore: probadas las tres, footystats
devuelve una shell de 14 bytes (los datos se piden por AJAX), la API de
sofascore responde 403 y ATP 403 por Cloudflare. La de fotmob responde 200
con JSON limpio en /api/data/.

Sirve para lo que pedia el usuario: medir el % real de un mercado sobre los
ULTIMOS PARTIDOS (ej: quantas veces marco el equipo 2+ goles en los ultimos
10), en vez de fiarse de la categoria del mercado.
"""
import json
import threading
import unicodedata
import time
from datetime import date, timedelta

import requests

BASE = "https://www.fotmob.com/api/data/"
H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.fotmob.com/",
}
_cache = {}
_lock = threading.Lock()
TTL = 1800  # 30 min: el historico no cambia cada minuto


def _get(ruta, params=None, ttl=None):
    clave = ruta + "?" + json.dumps(params or {}, sort_keys=True)
    ahora = time.time()
    # Los datos en vivo (matchDetails) cambian cada jugada: esas llamadas pasan
    # ttl corto. El historico sigue usando el TTL largo de arriba.
    vida = TTL if ttl is None else ttl
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < vida:
            return hit[1]
    try:
        r = requests.get(f"{BASE}{ruta}", params=params, headers=H, timeout=20)
        # r.json() falla con UnicodeDecodeError en algunas respuestas UTF-8
        # con caracteres de equipos, asi que se decodifica a mano desde bytes.
        datos = json.loads(r.content.decode("utf-8", "replace"))
    except Exception:
        datos = None
    with _lock:
        _cache[clave] = (ahora, datos)
    return datos


def _repara(t):
    """FotMob sirve los nombres con doble UTF-8: 'Bayern MÃ¼nchen'.

    Se repara pasando los bytes latin-1 por utf-8. Sin esto 'Bayern Munich' no
    encuentra 'Bayern München' y el equipo se pierde.
    """
    if not t:
        return ""
    try:
        return t.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t


def _clave(t):
    """Clave de comparacion sin acentos ni simbolos: 'Bayern München' -> 'bayernmunchen'."""
    t = _repara(t or "")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if c.isalnum()).lower()


def buscar_equipo(nombre):
    """'Arsenal' -> {'id': 9825, ...}. Devuelve None si no lo encuentra."""
    if not nombre:
        return None
    datos = _get("search/suggest", {"term": nombre})
    if not isinstance(datos, list):
        return None
    objetivo = _clave(nombre)
    candidatos = []
    for bloque in datos:
        for s in (bloque or {}).get("suggestions") or []:
            if s.get("type") in ("team", "country") and s.get("id"):
                candidatos.append(s)
    for s in candidatos:
        if _clave(s.get("name")) == objetivo:
            return s
    for s in candidatos:
        nombre_s = _clave(s.get("name"))
        if objetivo and (objetivo in nombre_s or nombre_s in objetivo):
            return s
    return candidatos[0] if candidatos else None


def _partidos_de_fecha(dia):
    datos = _get("matches", {"date": dia.strftime("%Y%m%d")})
    out = []
    for liga in (datos or {}).get("leagues") or []:
        for p in liga.get("matches") or []:
            # VERIFICADO contra la API: los partidos terminados traen
            # statusId 5 o 6 (6 es lo normal). El 2 que se puso al principio
            # no existe y devolvia 0 partidos en silencio.
            if str(p.get("statusId")) not in ("5", "6", "7"):
                continue
            h, a = p.get("home") or {}, p.get("away") or {}
            try:
                gf, ga = int(h.get("score")), int(a.get("score"))
            except (TypeError, ValueError):
                continue
            out.append({
                "id": p.get("id"),
                "date": dia.isoformat(),
                "home_id": h.get("id"), "away_id": a.get("id"),
                "home": h.get("name"), "away": a.get("name"),
                "gf": gf, "ga": ga,
            })
    return out


def ultimos_partidos(equipo_id, n: int = 10, dias_atras: int = 45):
    """Los ultimos N partidos TERMINADOS de un equipo, del mas reciente al mas viejo.

    Se recorre hacia atras por fecha porque /api/data/matches solo da un dia.
    Con 45 dias de margen hay de sobra para 10 partidos en ligas con partidos dos veces por semana.
    """
    equipo_id = int(equipo_id)
    out = []
    hoy = date.today()
    for offset in range(0, dias_atras):
        dia = hoy - timedelta(days=offset)
        for p in _partidos_de_fecha(dia):
            if p["home_id"] == equipo_id or p["away_id"] == equipo_id:
                p["local"] = (p["home_id"] == equipo_id)
                p["equipo_gol"] = p["gf"] if p["local"] else p["ga"]
                p["rival_gol"] = p["ga"] if p["local"] else p["gf"]
                out.append(p)
                if len(out) >= n:
                    return out[:n]
    return out


def prob_equipo_equipo(equipo_id, linea, lado="over", n: int = 10):
    """% real de los ultimos N partidos en los que el equipo marco >= linea."""
    if linea is None:
        return None
    partidos = ultimos_partidos(equipo_id, n)
    if len(partidos) < min(5, n):
        return None  # muestra insuficiente: no se afirma nada
    if lado == "over":
        aciertos = sum(1 for p in partidos if p["equipo_gol"] >= linea)
    else:
        aciertos = sum(1 for p in partidos if p["equipo_gol"] < linea)
    return int(round(aciertos * 100 / len(partidos)))


def prob_partido_ambos_marcan(equipo_id, n: int = 10):
    """BTTS: % de partidos donde marcaron ambos equipos."""
    partidos = ultimos_partidos(equipo_id, n)
    if len(partidos) < min(5, n):
        return None
    b = sum(1 for p in partidos if p["gf"] > 0 and p["ga"] > 0)
    return int(round(b * 100 / len(partidos)))



# ---------------------------------------------------------------------------
# RADAR DEL PARTIDO (mapa de tiros con xG, momentum y zonas de ataque)
# ---------------------------------------------------------------------------
# ESPN no manda coordenadas: el detalle de un partido de futbol no dice donde
# fue cada tiro. FotMob si (content.shotmap.shots con x/y y expectedGoals), y
# ademas trae el momentum minuto a minuto y las zonas de ataque.
#
# Lo que NO hay es la posicion de los jugadores ni del balon en vivo: eso es
# tracking optico (Hawk-Eye / Second Spectrum) y solo se consigue pagando la
# licencia del torneo. Por eso el "radar" es un mapa real de tiros y ataques.
TTL_VIVO = 45  # matchDetails: 45 s (en vivo cambia cada jugada)


def _partido_de_fecha(datos, home, away):
    """Busca el partido home-away dentro de la respuesta de /matches?date=."""
    objetivo_h, objetivo_a = _clave(home), _clave(away)
    if not objetivo_h or not objetivo_a:
        return None
    for liga in (datos or {}).get("leagues") or []:
        for p in liga.get("matches") or []:
            h = _clave((p.get("home") or {}).get("name"))
            a = _clave((p.get("away") or {}).get("name"))
            if not h or not a:
                continue
            # Se acepta cualquiera de los dos ordenes y nombres parciales
            # ("Atletico Madrid" vs "Atletico de Madrid").
            if (objetivo_h in h or h in objetivo_h) and (objetivo_a in a or a in objetivo_a):
                return p
            if (objetivo_h in a or a in objetivo_h) and (objetivo_a in h or h in objetivo_a):
                return p
    return None


def buscar_partido(home, away, fecha=None, dias=(0, -1, 1)):
    """id de FotMob del partido, buscando hoy, ayer y manana."""
    base = fecha or date.today()
    for delta in dias:
        dia = base + timedelta(days=delta)
        datos = _get("matches", {"date": dia.strftime("%Y%m%d")}, ttl=600)
        p = _partido_de_fecha(datos, home, away)
        if p and p.get("id"):
            return p["id"]
    return None


def radar(partido_id):
    """Datos del radar de un partido (tiros con xG, momentum, zonas, stats).

    Devuelve None si FotMob no responde o el id no existe.
    """
    md = _get("matchDetails", {"matchId": partido_id}, ttl=TTL_VIVO)
    if not isinstance(md, dict):
        return None

    content = md.get("content") or {}
    header = md.get("header") or {}
    status = header.get("status") or {}
    equipos = header.get("teams") or []
    home = equipos[0] if equipos else {}
    away = equipos[1] if len(equipos) > 1 else {}

    # teamId de FotMob -> "home"/"away"
    lado = {}
    for clave, eq in (("home", home), ("away", away)):
        try:
            lado[int(eq.get("id"))] = clave
        except (TypeError, ValueError):
            continue

    tiros = []
    for s in (content.get("shotmap") or {}).get("shots") or []:
        try:
            x = float(s.get("x"))
            y = float(s.get("y"))
        except (TypeError, ValueError):
            continue
        try:
            equipo = lado.get(int(s.get("teamId")), "home")
        except (TypeError, ValueError):
            equipo = "home"
        try:
            xg = round(float(s.get("expectedGoals") or 0), 3)
        except (TypeError, ValueError):
            xg = 0.0
        tiros.append({
            "x": round(x, 1),
            "y": round(y, 1),
            "xg": xg,
            "tipo": s.get("eventType") or "",
            "equipo": equipo,
            "jugador": s.get("playerName") or "",
            "min": s.get("min"),
            "bloqueado": bool(s.get("isBlocked")),
            "a_puerta": bool(s.get("isOnTarget")),
            "situacion": s.get("situation") or "",
        })

    momentum = []
    principal = ((content.get("momentum") or {}).get("main") or {}).get("data") or []
    for punto in principal:
        try:
            momentum.append({
                "min": int(punto.get("minute")),
                "valor": float(punto.get("value")),
            })
        except (TypeError, ValueError):
            continue

    stats = []
    periodos = (content.get("stats") or {}).get("Periods") or {}
    for grupo in (periodos.get("All") or {}).get("stats") or []:
        filas = []
        for s in grupo.get("stats") or []:
            vals = s.get("stats") or []
            if len(vals) < 2:
                continue
            filas.append({
                "label": s.get("title") or "",
                "home": vals[0],
                "away": vals[1],
            })
        if filas:
            stats.append({"grupo": grupo.get("title") or "", "filas": filas})

    if status.get("cancelled"):
        estado = "cancelado"
    elif status.get("finished"):
        estado = "final"
    elif status.get("started"):
        estado = "live"
    else:
        estado = "pre"

    return {
        "ok": True,
        "partido_id": partido_id,
        "estado": estado,
        "minuto": (status.get("liveTime") or {}).get("short") or "",
        "marcador": status.get("scoreStr") or "",
        "home": {
            "nombre": _repara(home.get("name")),
            "id": home.get("id"),
            "score": home.get("score"),
        },
        "away": {
            "nombre": _repara(away.get("name")),
            "id": away.get("id"),
            "score": away.get("score"),
        },
        "tiros": tiros,
        "momentum": momentum,
        "zonas": content.get("attackingZones") or {},
        "stats": stats,
    }
