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


def _get(ruta, params=None):
    clave = ruta + "?" + json.dumps(params or {}, sort_keys=True)
    ahora = time.time()
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < TTL:
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
