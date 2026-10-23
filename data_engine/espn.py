"""
Adaptador ESPN COMPLETO: busqueda de equipos, partidos y datos completos.

ESPN expone (gratis, sin key):
  - /apis/common/v3/search?query=X          -> busca equipos/ligas
  - /apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD-YYYYMMDD
  - /apis/site/v2/sports/{sport}/{league}/summary?event={id}  -> DATOS COMPLETOS
"""
import requests, os
from typing import Optional, Dict, Any, List

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_SEARCH = "https://site.api.espn.com/apis/common/v3/search"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

LIGAS_SOCCER = [
    "esp.1", "eng.1", "ita.1", "ger.1", "fra.1", "por.1", "ned.1",
    "mex.1", "arg.1", "bra.1", "usa.1", "col.1", "jpn.1", "bel.1",
    "uefa.champions", "uefa.europa", "conmebol.libertadores",
]

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _espn_path(sport: str, league: Optional[str]) -> str:
    """Convierte el nombre del deporte al path que usa ESPN en su API.

    ESPN usa URLs como `baseball/mlb/scoreboard`, `basketball/nba/scoreboard`.
    El primer segmento es el deporte (baseball, basketball, football, hockey,
    soccer, tennis, mma) y el segundo es la liga (mlb, nba, nfl, nhl, esp.1).
    Sin este mapeo, `mlb` devolvía "mlb" en vez de "baseball/mlb" y ESPN
    respondía 404 (partido no encontrado -> pick "Sin datos").
    """
    s = (sport or "").lower()
    if s in ("soccer", "futbol", "football_soccer"):
        return "soccer/" + (league or "esp.1")
    if s in ("nfl", "americanfootball", "american_football"):
        return "football/nfl"
    if s == "ncaaf" or s == "college_football":
        return "football/college-football"
    if s == "nba":
        return "basketball/nba"
    if s == "wnba":
        return "basketball/wnba"
    if s == "ncaab" or s == "mens_college_basketball":
        return "basketball/mens-college-basketball"
    if s == "mlb" or s == "baseball":
        return "baseball/mlb"
    if s == "nhl" or s == "hockey":
        return "hockey/nhl"
    if s == "tennis":
        return "tennis/atp"
    if s == "mma" or s == "ufc":
        return "mma/ufc"
    if s == "golf":
        return "golf/pga"
    if s == "boxing":
        return "boxing/boxing"
    if s == "cricket":
        return "cricket/icc"
    if s == "rugby":
        return "rugby/nzl"
    # Fallback: si no lo conocemos, usamos el nombre tal cual (puede fallar).
    return s

def buscar_equipo(nombre: str) -> Optional[Dict[str, Any]]:
    """Busca un equipo por nombre en ESPN. Devuelve {id, name, league, sport}."""
    try:
        r = requests.get(ESPN_SEARCH, params={"query": nombre, "limit": 10},
                         headers=UA, timeout=15)
        if r.status_code != 200:
            return None
        for item in r.json().get("items", []):
            if item.get("type") == "team":
                return {
                    "id": item.get("id"),
                    "name": item.get("displayName"),
                    "league": item.get("defaultLeagueSlug"),
                    "sport": item.get("sport"),
                }
        return None
    except Exception as e:
        print(f"[espn buscar_equipo] {e}")
        return None

def scoreboard(sport: str, league: str, dates: Optional[str] = None) -> List[Dict]:
    """Lista de eventos del scoreboard de una liga."""
    try:
        url = f"{ESPN_BASE}/{_espn_path(sport, league)}/scoreboard"
        params = {"dates": dates} if dates else {}
        r = requests.get(url, params=params, headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        return r.json().get("events", [])
    except Exception as e:
        print(f"[espn scoreboard] {e}")
        return []

def _parecido(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    pa = {w for w in a.split() if len(w) > 2}
    pb = {w for w in b.split() if len(w) > 2}
    return bool(pa & pb)

def _evento_coincide(ev: Dict, home: str, away: str) -> bool:
    comps = (ev.get("competitions") or [{}])[0]
    nombres = [c.get("team", {}).get("displayName", "") for c in comps.get("competitors", [])]
    return (any(_parecido(n, home) for n in nombres) and
            any(_parecido(n, away) for n in nombres))

def buscar_partido(sport: str, home: str, away: str,
                   fecha: Optional[str] = None, league: Optional[str] = None) -> Optional[Dict]:
    """Busca un partido por nombres. Escanea las ligas conocidas si no se da una."""
    ligas = [league] if league else (LIGAS_SOCCER if sport.lower() == "soccer" else [None])
    for lg in ligas:
        if not lg:
            continue
        for ev in scoreboard(sport, lg, dates=fecha):
            if _evento_coincide(ev, home, away):
                return {"event_id": ev.get("id"), "league": lg, "event": ev}
    return None

# ---------- DATOS COMPLETOS ----------

def datos_completos(sport: str, home: str, away: str,
                    fecha: Optional[str] = None, league: Optional[str] = None) -> Dict[str, Any]:
    """
    MOTOR DE BUSQUEDA: junta TODO lo disponible de un partido en ESPN.

    Si no se da fecha, usa la fecha actual en Eastern Time (no UTC), porque
    ESPN agrupa eventos por fecha del Este. Un partido del 2026-10-21T01:30Z
    es 2026-10-20T20:30 ET y pertenece al día 20 en ESPN.
    """
    if not fecha:
        # Usar Eastern Time, no UTC (ESPN agrupa por fecha del Este)
        from datetime import timedelta, timezone as tz
        now_utc = datetime.utcnow()
        try:
            from zoneinfo import ZoneInfo
            tz_eastern = ZoneInfo("America/New_York")
            now_eastern = now_utc.replace(tzinfo=tz.utc).astimezone(tz_eastern)
            fecha = now_eastern.strftime("%Y-%m-%d")
        except (ImportError, Exception):
            # Fallback: calcular offset ET manualmente (DST simplificado)
            # En invierno UTC-5, en verano UTC-4
            # Aproximación: usar UTC-5 como default (funciona la mayor parte del año)
            fecha = (now_utc - timedelta(hours=5)).strftime("%Y-%m-%d")
    partido = buscar_partido(sport, home, away, fecha, league)
    if not partido:
        return {"found": False, "raw": {}}

    event_id = partido["event_id"]
    lg = partido["league"]
    ev = partido["event"]

    try:
        url = f"{ESPN_BASE}/{_espn_path(sport, lg)}/summary"
        r = requests.get(url, params={"event": event_id}, headers=UA, timeout=20)
        if r.status_code != 200:
            return {"found": False, "raw": {}}
        summ = r.json()
    except Exception as e:
        print(f"[espn summary] {e}")
        return {"found": False, "raw": {}}

    data = _parse_summary(summ, ev)
    data["found"] = True
    data["source"] = "espn"
    data["event_id"] = event_id
    data["league"] = lg
    data["raw"] = summ
    return data

def _parse_summary(summ: Dict, ev: Dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    header = summ.get("header", {})
    comps = (header.get("competitions") or [{}])[0]
    competitors = comps.get("competitors", [])
    equipos = []
    for c in competitors:
        team = c.get("team", {})
        equipos.append({
            "name": team.get("displayName"),
            "abbr": team.get("abbreviation"),
            "home_away": c.get("homeAway"),
            "score": _to_float(c.get("score")),
            "winner": c.get("winner"),
            # Marcador por periodo (linescores): imprescindible para verificar
            # mercados de mitad/cuarto ('gana cualquier mitad', '1a mitad 1x2'),
            # que el marcador final NO determina.
            "linescores": c.get("linescores") or [],
        })
    out["equipos"] = equipos
    out["estado"] = (comps.get("status") or {}).get("type", {}).get("state")
    out["fecha"] = ev.get("date")
    out["nombre"] = ev.get("name")

    for e in equipos:
        if e.get("home_away") == "home":
            out["home_name"] = e["name"]; out["home_score"] = e["score"]
        elif e.get("home_away") == "away":
            out["away_name"] = e["name"]; out["away_score"] = e["score"]

    def _periodos(lado):
        """Goles/puntos del equipo por periodo (1a mitad, 2a mitad, ...)."""
        for e in equipos:
            if e.get("home_away") == lado:
                vals = []
                for p in e.get("linescores") or []:
                    v = p.get("displayValue", p.get("value")) if isinstance(p, dict) else p
                    v = _to_float(v)
                    vals.append(v if v is not None else 0.0)
                return vals
        return []
    out["home_periodos"] = _periodos("home")
    out["away_periodos"] = _periodos("away")
    if out["home_periodos"] and out["away_periodos"]:
        # Conveniencia: marcador al descanso. En deportes de 4 cuartos la 1a
        # mitad es la suma de los dos primeros periodos.
        hp, ap = out["home_periodos"], out["away_periodos"]
        n = min(len(hp), len(ap))
        corte = n // 2 if n >= 4 else 1
        out["home_ht"] = sum(hp[:corte])
        out["away_ht"] = sum(ap[:corte])
    if out.get("home_score") is not None and out.get("away_score") is not None:
        if out["home_score"] > out["away_score"]:
            out["winner"] = "home"
        elif out["away_score"] > out["home_score"]:
            out["winner"] = "away"
        else:
            out["winner"] = "draw"

    box = summ.get("boxscore", {})
    stats_equipos = {}
    for t in box.get("teams", []):
        nombre = t.get("team", {}).get("displayName")
        stats = {}
        for s in t.get("statistics", []):
            stats[s.get("name")] = _to_float(s.get("displayValue"))
        stats_equipos[nombre] = stats
    out["stats_equipos"] = stats_equipos

    def stat(lado, key):
        eq = out.get(f"{lado}_name")
        return (stats_equipos.get(eq) or {}).get(key)

    out["home_corners"] = stat("home", "wonCorners")
    out["away_corners"] = stat("away", "wonCorners")
    out["total_corners"] = (out["home_corners"] or 0) + (out["away_corners"] or 0) \
        if out["home_corners"] is not None and out["away_corners"] is not None else None
    out["home_yellow"] = stat("home", "yellowCards")
    out["away_yellow"] = stat("away", "yellowCards")
    out["home_red"] = stat("home", "redCards")
    out["away_red"] = stat("away", "redCards")
    hy, ay = out["home_yellow"], out["away_yellow"]
    hr, ar = out["home_red"], out["away_red"]
    out["total_cards"] = (hy + ay + hr + ar) if None not in (hy, ay, hr, ar) else None
    out["home_fouls"] = stat("home", "foulsCommitted")
    out["away_fouls"] = stat("away", "foulsCommitted")
    out["total_fouls"] = (out["home_fouls"] or 0) + (out["away_fouls"] or 0) \
        if out["home_fouls"] is not None and out["away_fouls"] is not None else None
    out["home_shots"] = stat("home", "totalShots")
    out["away_shots"] = stat("away", "totalShots")
    out["home_shots_on_target"] = stat("home", "shotsOnTarget")
    out["away_shots_on_target"] = stat("away", "shotsOnTarget")
    out["home_saves"] = stat("home", "saves")
    out["away_saves"] = stat("away", "saves")
    out["home_possession"] = stat("home", "possessionPct")
    out["away_possession"] = stat("away", "possessionPct")
    return out

def _parse_jugadores(summ: Dict) -> Dict[str, Any]:
    """Extrae rosters (jugadores con stats) y leaders de ESPN."""
    out: Dict[str, Any] = {}

    jugadores = []
    for roster in (summ.get("rosters") or []):
        team_name = (roster.get("team") or {}).get("displayName")
        for p in roster.get("roster", []):
            ath = p.get("athlete", {})
            entry = {
                "nombre": ath.get("displayName"),
                "equipo": team_name,
                "posicion": (ath.get("position") or {}).get("abbreviation"),
                "titular": p.get("starter"),
                "stats": {},
            }
            for s in (p.get("stats") or []):
                entry["stats"][s.get("name")] = _to_float(s.get("displayValue"))
            jugadores.append(entry)
    out["jugadores"] = jugadores

    leaders = []
    for grp in (summ.get("leaders") or []):
        team_name = (grp.get("team") or {}).get("displayName")
        for cat in grp.get("leaders", []):
            categoria = cat.get("displayName")
            for item in cat.get("leaders", []):
                ath = item.get("athlete", {}) or {}
                leaders.append({
                    "nombre": ath.get("displayName"),
                    "equipo": team_name,
                    "categoria": categoria,
                    "valor": item.get("displayValue"),
                })
    out["leaders"] = leaders
    return out

def _parse_eventos(summ: Dict) -> Dict[str, Any]:
    """Extrae eventos clave (goles, tarjetas) y odds."""
    out: Dict[str, Any] = {}
    eventos = []
    for ke in (summ.get("keyEvents") or []):
        participantes = ke.get("participants") or []
        jugador = None
        if participantes and isinstance(participantes[0], dict):
            jugador = (participantes[0].get("athlete") or {}).get("displayName")
        eventos.append({
            "tipo": (ke.get("type") or {}).get("text"),
            "minuto": (ke.get("clock") or {}).get("displayValue"),
            "equipo": (ke.get("team") or {}).get("displayName"),
            "jugador": jugador,
        })
    out["eventos_clave"] = eventos

    odds = []
    for o in (summ.get("odds") or []):
        odds.append({
            "proveedor": (o.get("provider") or {}).get("name"),
            "detalle": o.get("details"),
            "over_under": o.get("overUnder"),
        })
    out["odds"] = odds
    return out

def datos_completos_completo(sport: str, home: str, away: str,
                             fecha: Optional[str] = None, league: Optional[str] = None) -> Dict[str, Any]:
    """Version extendida: agrega jugadores, eventos y odds."""
    base = datos_completos(sport, home, away, fecha, league)
    if not base.get("found"):
        return base
    raw = base.get("raw", {})
    base.update(_parse_jugadores(raw))
    base.update(_parse_eventos(raw))
    return base


