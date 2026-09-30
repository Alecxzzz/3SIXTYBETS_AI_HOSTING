"""
PROBABILIDAD REAL de un pick, calculada sobre los ultimos partidos.

Regla del proyecto: un pick solo se publica si la probabilidad medida es de
70% o mas. Si no llega, el generador RE-ANALIZA el partido buscando otro
mercado que si llegue. Sin esto se publicaban pronosticos que despues se
fallaban: el 'Phillies hits Under 3.5' tenia 40% de acierto real y no habia
forma de saberlo.

Datos: MLB -> statsapi.mlb.com (gameLog real). Soccer -> ESPN (marcadores).
"""
import re
import threading
import time

import requests

_cache = {}
_lock = threading.Lock()
TTL = 900  # 15 min


def _cached(clave, fn):
    ahora = time.time()
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < TTL:
            return hit[1]
    valor = fn()
    with _lock:
        _cache[clave] = (ahora, valor)
    return valor


MLB_BASE = "https://statsapi.mlb.com/api/v1"


def _mlb_json(path, params):
    try:
        r = requests.get(f"{MLB_BASE}{path}", params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def mlb_team_id(nombre_equipo: str):
    """'Philadelphia Phillies' -> 143. OJO: NO es 215 (ese no existe)."""
    def _buscar():
        data = _mlb_json("/teams", {"sportId": 1, "season": "2026"}) or {}
        objetivo = (nombre_equipo or "").lower()
        palabras = [p for p in re.sub(r"[^a-z ]", " ", objetivo).split() if len(p) > 2]
        for t in data.get("teams") or []:
            nom = (t.get("name") or "").lower()
            clave = (t.get("teamName") or "").lower()
            if objetivo and (objetivo in nom or clave in objetivo):
                return t.get("id")
            for w in palabras:
                if w and (w in clave or w in nom):
                    return t.get("id")
        return None
    return _cached(f"mlbteam:{nombre_equipo}", _buscar)


def mlb_player_id(nombre: str):
    def _buscar():
        data = _mlb_json("/people/search", {"names": nombre}) or {}
        people = data.get("people") or []
        if not people:
            return None
        apellido = (nombre or "").lower().split()[-1] if nombre else ""
        for p in people:
            if apellido and apellido in (p.get("fullName") or "").lower():
                return p.get("id")
        return people[0].get("id")
    return _cached(f"mlbplayer:{nombre}", _buscar)


def mlb_jugador_partidos(nombre: str, n: int = 10):
    pid = mlb_player_id(nombre)
    if not pid:
        return []

    def _cargar():
        data = _mlb_json(f"/people/{pid}/stats",
                         {"stats": "gameLog", "group": "hitting", "season": "2026"}) or {}
        splits = ((data.get("stats") or [{}])[0].get("splits") or [])
        out = []
        for s in splits[-n:]:
            st = s.get("stat") or {}
            fila = {}
            for k in ("hits", "runs", "homeRuns", "atBats", "walks",
                      "strikeOuts", "doubles", "triples", "rbi"):
                try:
                    fila[k] = int(st.get(k) or 0)
                except (TypeError, ValueError):
                    fila[k] = 0
            out.append(fila)
        return out
    return _cached(f"mlbgamelog:{pid}:{n}", _cargar)


def mlb_equipo_partidos(nombre_equipo: str, n: int = 10):
    tid = mlb_team_id(nombre_equipo)
    if not tid:
        return []

    def _cargar():
        data = _mlb_json(f"/teams/{tid}/stats",
                         {"stats": "gameLog", "group": "hitting", "season": "2026"}) or {}
        splits = ((data.get("stats") or [{}])[0].get("splits") or [])
        out = []
        for s in splits[-n:]:
            st = s.get("stat") or {}
            fila = {}
            for k in ("runs", "hits", "atBats", "walks", "strikeOuts",
                      "doubles", "triples", "rbi"):
                try:
                    fila[k] = int(st.get(k) or 0)
                except (TypeError, ValueError):
                    fila[k] = 0
            out.append(fila)
        return out
    return _cached(f"mlbequipogame:{tid}:{n}", _cargar)


ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
# ESPN no usa soccer/ para el hockey: el hockey nhl vive en hockey/nhl.
# Con soccer/nhl el scoreboard devuelve 0 eventos en silencio.
_DEPORTE_EN_ESPN = {"hockey": "hockey", "nhl": "hockey", "nba": "basketball"}


def _ruta_espn(league):
    """Ruta correcta de ESPN para una liga. Sin esto el hockey devuelve 0."""
    if not league:
        return None
    raiz = _DEPORTE_EN_ESPN.get(str(league).lower(), "soccer")
    return f"{ESPN_BASE}/{raiz}/{league}"


def soccer_ultimos_marcadores(league: str):
    if not league:
        return []

    def _cargar():
        ruta = _ruta_espn(league)
        if not ruta:
            return []
        try:
            r = requests.get(f"{ruta}/scoreboard", timeout=18)
            events = (r.json() or {}).get("events") or []
        except Exception:
            return []
        out = []
        for ev in events:
            comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
            if len(comps) != 2:
                continue
            def _g(c):
                try:
                    return int(float(c.get("score") or 0))
                except (TypeError, ValueError):
                    return 0
            a, b = comps[0], comps[1]
            out.append({
                "home": (a.get("team") or {}).get("displayName") or "",
                "away": (b.get("team") or {}).get("displayName") or "",
                "gf": _g(a), "ga": _g(b),
            })
        return out
    return _cached(f"espnscore:{league}", _cargar)


def soccer_prob_equipo_marca(league, nombre_equipo, linea, n=10):
    """% de los ultimos N partidos en los que el equipo marco >= linea."""
    if linea is None or linea <= 0:
        return 100
    partidos = soccer_ultimos_marcadores(league)
    if not partidos:
        return None
    clave = (nombre_equipo or "").lower()
    cuenta = total = 0
    for p in partidos:
        if clave and clave in p["home"].lower():
            goals = p["gf"]
        elif clave and clave in p["away"].lower():
            goals = p["ga"]
        else:
            continue
        total += 1
        if goals >= linea:
            cuenta += 1
        if total >= n:
            break
    if total < 5:
        return None
    return int(round(cuenta * 100 / total))


def _linea_de(texto):
    """(lado, linea) desde 'Over 1.5' / 'Under 3.5' / '1+'."""
    t = (texto or "").lower()
    m = re.search(r"(over|mas de|m[aá]s de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", t)
    if m:
        return "over", float(m.group(2).replace(",", "."))
    m = re.search(r"(under|menos de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", t)
    if m:
        return "under", float(m.group(2).replace(",", "."))
    m = re.search(r"\b([0-9]+)\s*\+", t)
    if m:
        return "over_plus", int(m.group(1))
    return None, None


_STAT_POR_PALABRA = (
    ("jonron", "homeRuns"), (" hr", "homeRuns"), ("hr ", "homeRuns"),
    ("hit", "hits"), ("ponche", "strikeOuts"), ("strikeout", "strikeOuts"),
    ("carrera", "runs"),
)


def probabilidad_pick(pick, sport, league=None, n=10):
    """Probabilidad REAL (%) del pick en los ultimos N partidos.

    Devuelve 0-100, o None si no hay datos suficientes.
    """
    texto = " ".join([str(pick.get("market") or ""), str(pick.get("titulo") or ""),
                      str(pick.get("selection") or "")]).lower()
    lado, linea = _linea_de(texto)
    if lado is None:
        return None

    def _cumple(v):
        if lado in ("over", "over_plus"):
            return v >= linea
        return v < linea

    if sport == "mlb":
        jugador = re.sub(r"\d.*$", "", str(pick.get("selection") or "")).strip()
        stat = None
        for palabra, campo in _STAT_POR_PALABRA:
            if palabra in texto:
                stat = campo
                break
        es_prop = jugador and any(p in texto for p in ("hit", "jonron", "hr", "ponche", "strikeout"))
        if es_prop and stat:
            partidos = mlb_jugador_partidos(jugador, n)
            # Si no hay partido del jugador (p.ej. 'PHI Phillies', que es un
            # EQUIPO y no un jugador) se sigue al calculo por equipo en vez de
            # devolver None y perder la medicion.
            if partidos:
                return int(round(sum(1 for p in partidos if _cumple(p.get(stat, 0))) * 100 / len(partidos)))
        equipo = pick.get("homeName") or pick.get("home_name") or ""
        partidos = mlb_equipo_partidos(equipo, n)
        if not partidos:
            return None
        stat = "runs" if ("carrera" in texto or "total" in texto or "runs" in texto) else "hits"
        return int(round(sum(1 for p in partidos if _cumple(p.get(stat, 0))) * 100 / len(partidos)))

    if sport in ("soccer", "futbol", "football"):
        equipo = (pick.get("homeName") or pick.get("home_name")
                   or pick.get("equipo") or "")
        # 'Over 1.5 goles de X' / 'X Over 1.5': el equipo puede venir en el
        # titulo y no en los campos del pick, asi que se busca ahi tambien.
        if not equipo:
            for campo in ("titulo", "selection"):
                m = re.search(r"([A-Za-z\u00c0-\u017f][\w\u00c0-\u017f .'-]{2,40}?)\s+(?:Over|Under|Mas de|Menos de|M[a\u00e1]s de)", str(pick.get(campo) or ""), re.I)
                if m:
                    equipo = m.group(1).strip()
                    break
        if not equipo or linea is None:
            return None
        valor = float(linea) if lado != "over_plus" else float(linea)
        try:
            import fotmob
        except Exception:
            return None
        eq = fotmob.buscar_equipo(equipo)
        if not eq:
            return soccer_prob_equipo_marca(league, equipo, valor, n) if league else None
        # BTTS no tiene linea: se mide aparte
        if "btts" in texto or "ambos equipos" in texto:
            return fotmob.prob_partido_ambos_marcan(eq["id"], n)
        return fotmob.prob_equipo_equipo(eq["id"], valor, lado, n)

    return None


def linea_optima_por_promedio(valores, lado_buscado="over"):
    """Linea que maximiza el acierto, a partir del historico.

    Responde a 'saco el promedio y busco la cantidad exacta': prueba lineas
    alrededor del promedio y devuelve la que mas acierta, con su %.
    """
    if not valores:
        return None, 0
    promedio = sum(valores) / len(valores)
    candidatas = {promedio}
    for v in valores:
        candidatas.add(float(v))
    for c in list(candidatas):
        candidatas.add(round(c, 2))
    mejor = (None, 0)
    for linea in candidatas:
        # 'Over 0' es siempre cierto (100% vacio): no es una apuesta, asi que
        # una linea de 0 no puede ganar el KT.
        if lado_buscado == "over" and linea <= 0:
            continue
        if lado_buscado == "over":
            pct = sum(1 for v in valores if v >= linea) * 100 / len(valores)
        else:
            pct = sum(1 for v in valores if v < linea) * 100 / len(valores)
        if pct > mejor[1]:
            mejor = (round(linea, 2), int(round(pct)))
    return mejor
