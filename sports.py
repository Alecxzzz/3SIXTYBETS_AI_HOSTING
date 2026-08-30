"""
Modulo de estadisticas deportivas en vivo usando la API publica de ESPN.
Cubre: futbol (soccer), NBA, MLB, NFL y tenis.

Sin API key, sin costo. Cache en memoria con TTL para no golpear la API
mas de lo necesario (ESPN refresca sus marcadores cada ~15s).
"""

import time
import threading
from datetime import datetime, timezone

import requests as http_requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Ligas de futbol por pais/region (codigo ESPN -> nombre legible)
SOCCER_LEAGUES = {
    # Europa
    "eng.1": "Premier League (Inglaterra)",
    "esp.1": "LaLiga (España)",
    "ita.1": "Serie A (Italia)",
    "ger.1": "Bundesliga (Alemania)",
    "fra.1": "Ligue 1 (Francia)",
    "por.1": "Primeira Liga (Portugal)",
    "ned.1": "Eredivisie (Holanda)",
    "uefa.champions": "Champions League",
    "uefa.europa": "Europa League",
    "uefa.europa.conf": "Conference League",
    # Americas
    "mex.1": "Liga MX (Mexico)",
    "usa.1": "MLS (USA)",
    "arg.1": "Liga Profesional (Argentina)",
    "bra.1": "Brasileirao (Brasil)",
    "col.1": "Liga BetPlay (Colombia)",
    "conmebol.libertadores": "Copa Libertadores",
    # Asia/Other
    "sau.1": "Saudi Pro League",
    "jpn.1": "J-League (Japon)",
}

# Deportes soportados: clave -> (path ESPN, etiqueta)
SPORTS = {
    "soccer": ("soccer", "Futbol"),
    "nba": ("basketball/nba", "NBA"),
    "mlb": ("baseball/mlb", "MLB"),
    "nfl": ("football/nfl", "NFL"),
    "tennis": ("tennis/atp", "Tenis"),
    "mma": ("mma/ufc", "MMA/UFC"),
}

CACHE_TTL_SECONDS = 60
DETAIL_CACHE_TTL_SECONDS = 30

# Traduccion de nombres de estadisticas a espanol
STAT_TRANSLATIONS = {
    # Futbol
    "foulsCommitted": "Faltas",
    "yellowCards": "Tarjetas amarillas",
    "redCards": "Tarjetas rojas",
    "offsides": "Fueras de juego",
    "wonCorners": "Corners",
    "saves": "Atajadas",
    "possessionPct": "Posesion %",
    "totalShots": "Tiros totales",
    "shotsOnTarget": "Tiros a puerta",
    "shotPct": "% de tiro",
    "penaltyKickGoals": "Goles de penal",
    "penaltyKickShots": "Tiros de penal",
    "accuratePasses": "Pases precisos",
    "totalPasses": "Pases totales",
    "passPct": "% de pase",
    "accurateCrosses": "Centros precisos",
    "totalCrosses": "Centros totales",
    "crossPct": "% de centro",
    "tacklesWon": "Entradas ganadas",
    "totalTackles": "Entradas totales",
    "interceptions": "Intercepciones",
    "clearances": "Despejes",
    "aerialsWon": "Duelos aereos ganados",
    "goals": "Goles",
    "assists": "Asistencias",
    "ownGoals": "Autogoles",
    "conceded": "Goles recibidos",
    # NBA
    "fieldGoalsMade": "Tiros de campo anotados",
    "fieldGoalsAttempted": "Tiros de campo intentados",
    "fieldGoalPct": "% tiros de campo",
    "threePointFieldGoalsMade": "Triples anotados",
    "threePointFieldGoalsAttempted": "Triples intentados",
    "threePointFieldGoalPct": "% triples",
    "freeThrowsMade": "Tiros libres anotados",
    "freeThrowsAttempted": "Tiros libres intentados",
    "freeThrowPct": "% tiros libres",
    "rebounds": "Rebotes",
    "offensiveRebounds": "Rebotes ofensivos",
    "defensiveRebounds": "Rebotes defensivos",
    "steals": "Robos",
    "blocks": "Bloqueos",
    "turnovers": "Perdidas",
    "personalFouls": "Faltas personales",
    "fastBreakPoints": "Puntos de contragolpe",
    "pointsInThePaint": "Puntos en la pintura",
    "secondChancePoints": "Puntos de segunda oportunidad",
    "biggestLead": "Mayor ventaja",
    "benchPoints": "Puntos del banquillo",
    "leadChanges": "Cambios de liderato",
    "timesTied": "Veces empatadas",
    # NFL
    "totalYards": "Yardas totales",
    "netPassingYards": "Yardas por pase",
    "rushingYards": "Yardas terrestres",
    "firstDowns": "Primeros downs",
    "thirdDownEff": "Eficiencia 3er down",
    "fourthDownEff": "Eficiencia 4to down",
    "totalDrives": "Drives totales",
    "possessionTime": "Posesion",
    "timeOfPossession": "Posesion",
    "sacks": "Capturas (sacks)",
    "interceptionsThrown": "Intercepciones lanzadas",
    "fumbles": "Balones sueltos",
    "punts": "Despejes (punts)",
    "penalties": "Penalizaciones",
    "penaltyYards": "Yardas por penalizacion",
    "touchdowns": "Touchdowns",
    "completionAttempts": "Pases completados/intentados",
    "yardsPerPass": "Yardas por pase",
    "yardsPerRushAttempt": "Yardas por acarreo",
    # MLB
    "atBats": "Turnos al bate",
    "runs": "Carreras",
    "hits": "Hits",
    "runsBattedIn": "Carreras impulsadas",
    "homeRuns": "Home runs",
    "baseOnBalls": "Bases por bolas",
    "strikeouts": "Ponches",
    "stolenBases": "Bases robadas",
    "battingAverage": "Promedio de bateo",
    "obp": "OBP",
    "slg": "SLG",
    "ops": "OPS",
    "inningsPitched": "Entradas lanzadas",
    "earnedRunAverage": "Promedio de carreras limpias",
    "gamesPlayed": "Juegos jugados",
    "teamGamesPlayed": "Juegos del equipo",
    "doubles": "Dobles",
    "triples": "Triples",
    "hitByPitch": "Golpeado por lanzamiento",
    "sacrificeHits": "Toques de sacrificio",
    "groundBalls": "Rodados",
    "RBIs": "Carreras impulsadas",
    "leftOnBase": "Corredores dejados en base",
    "pitchingStrikeouts": "Ponches (pitching)",
    "pitchingHits": "Hits permitidos",
    "pitchingRuns": "Carreras permitidas",
    "pitchingEarnedRuns": "Carreras limpias",
    "pitchingBaseOnBalls": "Bases por bolas permitidas",
    "pitchingHomeRuns": "Home runs permitidos",
    "battersFaced": "Bateadores enfrentados",
    "era": "PCL (ERA)",
    "whip": "WHIP",
    "fieldingPct": "% fildeo",
    "errors": "Errores",
    "assists": "Asistencias",
    "putouts": "Ponches defensivos",
    "doublePlays": "Doble bases",
    "triplePlays": "Triple bases",
}

_cache = {}  # clave -> {"data": ..., "ts": epoch}
_cache_lock = threading.Lock()


def _cache_get(key, ttl=CACHE_TTL_SECONDS):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < ttl:
            return entry["data"]
    return None


def _cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}


def _date_range() -> str:
    """Rango de fechas hoy+manana para que salgan proximos partidos."""
    from datetime import timedelta
    today = datetime.now(timezone.utc)
    tomorrow = today + timedelta(days=1)
    return f"{today.strftime('%Y%m%d')}-{tomorrow.strftime('%Y%m%d')}"


# Cabeceras de navegador: ESPN/Akamai bloquea el User-Agent por defecto de
# python-requests con 403 "Access Denied". Sin esto NO cargan los partidos.
ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://www.espn.com/",
}


ESPN_HOSTS = [
    "https://site.api.espn.com/apis/site/v2/sports",
    "https://site.web.api.espn.com/apis/site/v2/sports",
]


_espn_host_idx = {"i": 0}  # host preferente (sticky): si el primario bloquea, se queda en el mirror


def _espn_get(url: str, params: dict | None = None, timeout: int = 10):
    """GET a ESPN con cabeceras de navegador, mirror y host preferente.

    Akamai bloquea IPs de datacenter con 403. Se prueba el host preferente
    (empieza siendo site.api) y si responde 403/429 se pasa al mirror
    site.web.api.espn.com (misma estructura). El host que funciona queda
    "pegado" para no repetir los intentos fallidos en cada llamada.
    """
    import time as _time
    hosts = ESPN_HOSTS if url.startswith(ESPN_HOSTS[0]) else [None]
    last_exc = None
    orden = hosts[_espn_host_idx["i"] % len(hosts):] + hosts[:_espn_host_idx["i"] % len(hosts)] if len(hosts) > 1 else hosts
    for host in orden:
        u = url.replace(ESPN_HOSTS[0], host) if host else url
        for intento in range(2):
            try:
                resp = http_requests.get(u, params=params, timeout=timeout, headers=ESPN_HEADERS)
                if resp.status_code in (403, 429) and intento < 1:
                    _time.sleep(1.2)
                    continue
                if resp.status_code in (403, 429):
                    break  # siguiente host
                resp.raise_for_status()
                # Host que funciona -> preferente para siguientes llamadas
                if host:
                    _espn_host_idx["i"] = ESPN_HOSTS.index(host)
                return resp
            except Exception as e:
                last_exc = e
                if intento < 1:
                    _time.sleep(1.2)
    if last_exc is None:
        last_exc = Exception("ESPN bloqueado (403/429) en todos los hosts")
    raise last_exc


def _fetch_scoreboard(path: str, league: str | None = None, params_extra: dict | None = None) -> dict:
    # En soccer la liga va en el path (soccer/eng.1/scoreboard), no como query param
    if league and path.startswith("soccer"):
        url = f"{ESPN_BASE}/{path}/{league}/scoreboard"
    else:
        url = f"{ESPN_BASE}/{path}/scoreboard"
    params = {}
    # El tenis anida los torneos en un solo evento; si filtramos por fechas
    # perdemos los torneos en curso (ej. US Open). Sin dates trae todo.
    if not path.startswith("tennis"):
        params["dates"] = _date_range()
    if params_extra:
        params.update(params_extra)
    resp = _espn_get(url, params=params)
    return resp.json()


def _fetch_json(url: str) -> dict:
    resp = _espn_get(url)
    return resp.json()


def _parse_number(value):
    """Convierte '2', 2.0, '87.5' a numero cuando tiene sentido."""
    if value in (None, ""):
        return None
    try:
        num = float(value)
        return int(num) if num == int(num) else num
    except (TypeError, ValueError):
        return value


def _parse_team(team: dict) -> dict:
    """Normaliza un equipo de la respuesta de ESPN."""
    raw_score = team.get("score")
    if isinstance(raw_score, list):
        raw_score = raw_score[0].get("value") if raw_score else None

    logo = team.get("logo")
    if isinstance(logo, list):
        logo = logo[0] if logo else None

    color = team.get("color")
    alt_color = team.get("alternateColor")

    return {
        "id": team.get("id"),
        "name": team.get("displayName", team.get("name", "?")),
        "short_name": team.get("shortDisplayName", team.get("name", "?")),
        "abbr": team.get("abbreviation", ""),
        "logo": logo,
        "color": f"#{color}" if color else None,
        "alt_color": f"#{alt_color}" if alt_color else None,
        "score": _parse_number(raw_score),
        "winner": team.get("winner"),
    }


def get_team_recent_events(sport: str, league: str | None, team_id, limit: int = 6) -> list:
    """Ultimos eventos finalizados de un equipo, via scoreboard por rango de fechas.

    Es el mecanismo mas confiable (el mismo scoreboard que usa el resto del
    sitio) y sirve de respaldo cuando el summary de ESPN no trae los ultimos
    partidos del equipo (pasa en futbol). Devuelve eventos crudos de ESPN.
    """
    from datetime import timedelta
    path, _label = SPORTS[sport]
    cache_key = f"team_recent:{sport}:{league or ''}:{team_id}:{limit}"
    cached = _cache_get(cache_key, ttl=900)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc)
    start = today - timedelta(days=45)
    params = {"dates": f"{start.strftime('%Y%m%d')}-{today.strftime('%Y%m%d')}"}
    url = (
        f"{ESPN_BASE}/{path}/{league}/scoreboard"
        if (league and path.startswith("soccer"))
        else f"{ESPN_BASE}/{path}/scoreboard"
    )
    resp = _espn_get(url, params=params)
    data = resp.json()

    events = []
    for ev in data.get("events", []):
        state = (ev.get("status") or {}).get("type", {}).get("state", "")
        if state != "post":
            continue
        comp0 = (ev.get("competitions") or [{}])[0]
        ids = {str((c.get("team") or {}).get("id")) for c in comp0.get("competitors", [])}
        if str(team_id) in ids:
            events.append(ev)
    events.sort(key=lambda e: e.get("date", ""))
    events = events[-limit:]
    _cache_set(cache_key, events)
    return events


def _recent_from_events(events: list) -> list:
    """Convierte eventos crudos de ESPN al formato recent_games del detalle."""
    recent = []
    for ev in events:
        comp0 = (ev.get("competitions") or [{}])[0]
        tmap = {}
        for c in comp0.get("competitors", []):
            tmap[c.get("homeAway")] = {
                "name": (c.get("team") or {}).get("displayName", "?"),
                "score": _parse_number(c.get("score")),
                "winner": c.get("winner"),
            }
        recent.append({
            "date": ev.get("date"),
            "status": (comp0.get("status") or {}).get("type", {}).get("shortDetail", ""),
            "home": tmap.get("home", {}),
            "away": tmap.get("away", {}),
        })
    return recent


def _linescores(competitor: dict) -> list:
    """Scores por periodo: innings (MLB), cuartos (NBA/NFL), etc."""
    out = []
    for ls in competitor.get("linescores") or []:
        if ls:
            out.append(str(ls.get("displayValue", ls.get("value", ""))))
    return out


def _parse_event(event: dict, league_label: str, league_code: str | None) -> dict:
    comp = (event.get("competitions") or [{}])[0]

    home, away = {}, {}
    home_linescores, away_linescores = [], []
    for competitor in comp.get("competitors", []):
        parsed = _parse_team(competitor.get("team") or {}) or {}
        # Fallback MMA/UFC: cuando no hay "team", el dato viene en "athlete"
        if parsed.get("name") in (None, "?", "") and competitor.get("athlete"):
            athlete = competitor["athlete"]
            parsed["name"] = athlete.get("displayName") or athlete.get("fullName") or "?"
            parsed["short_name"] = athlete.get("shortName") or parsed["name"]
            parsed["abbr"] = athlete.get("shortName") or ""
            _al = athlete.get("logo")
            if isinstance(_al, list):
                _al = _al[0] if _al else None
            parsed["logo"] = _al
            parsed["id"] = athlete.get("id") or parsed["id"]
        # Fallback: a veces el score viene en el competitor y no en team
        if parsed["score"] is None:
            parsed["score"] = _parse_number(competitor.get("score"))
        recs = [r for r in (competitor.get("records") or []) if r]
        parsed["record"] = recs[0].get("summary") if recs else None
        lines = _linescores(competitor)
        ha = competitor.get("homeAway")
        # homeAway="home" -> local; homeAway="away" -> visitante.
        # Si ESPN no marca homeAway (peleas MMA/UFC), el primero es local.
        if ha == "home":
            home = parsed
            home_linescores = lines
        elif ha == "away" or home:
            away = parsed
            away_linescores = lines
        else:
            # Primer competidor sin homeAway -> local (home)
            home = parsed
            home_linescores = lines

    status = event.get("status", {})
    type_info = status.get("type", {})
    state = type_info.get("state")  # pre | in | post

    display_clock = status.get("displayClock") or ""
    short_detail = type_info.get("shortDetail", "")

    # Cuotas si ESPN las trae
    odds = None
    for item in comp.get("odds") or []:
        if not item:
            continue
        odds = {
            "details": item.get("details"),
            "over_under": item.get("overUnder"),
            "home_odds": (item.get("homeTeamOdds") or {}).get("moneyLine"),
            "away_odds": (item.get("awayTeamOdds") or {}).get("moneyLine"),
        }
        break

    return {
        "id": event.get("id"),
        "sport_path": None,  # se llena en get_sport_games
        "league_code": league_code,
        "league": league_label,
        "date": event.get("date"),
        "name": event.get("name", ""),
        "state": state,  # pre = proximo, in = en vivo, post = finalizado
        "status": short_detail,
        "clock": display_clock,
        "period": status.get("period"),
        "home": home,
        "away": away,
        "home_linescores": home_linescores,
        "away_linescores": away_linescores,
        "odds": odds,
        "summary": event.get("links", [{}])[0].get("href"),
    }


def _parse_tennis(events: list) -> list:
    """Parsea tenis de ESPN.

    Estructura real: cada evento es un TORNEO (ej. US Open) y los partidos
    viven en event.groupings[*].competitions[*]. Los competidores usan
    'athlete' (no 'team'). Se muestran solo partidos en vivo y proximos.
    """
    from datetime import datetime, timedelta, timezone
    limite = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y%m%d%H%M%S")

    def _player(competitor: dict) -> dict:
        ath = competitor.get("athlete") or {}
        name = ath.get("displayName") or ath.get("fullName") or "?"
        flag = ath.get("flag") or ath.get("logo")
        if isinstance(flag, list):
            flag = flag[0] if flag else None
        return {
            "id": ath.get("id"),
            "name": name,
            "short_name": ath.get("shortName") or name,
            "abbr": ath.get("shortName") or "",
            "logo": flag,
            "color": None,
            "alt_color": None,
            "score": _parse_number(competitor.get("score")),
            "winner": competitor.get("winner"),
        }

    out = []
    for event in events:
        torneo = event.get("shortName") or event.get("name") or "Tenis"
        groupings = event.get("groupings") or []
        comps_iter = []
        if groupings:
            for g in groupings:
                for comp in g.get("competitions") or []:
                    comps_iter.append((g.get("grouping", {}).get("displayName", ""), comp))
        else:
            for comp in event.get("competitions") or []:
                comps_iter.append(("", comp))

        for rama, comp in comps_iter:
            competitors = comp.get("competitors") or []
            if len(competitors) < 2:
                continue
            status = comp.get("status") or event.get("status") or {}
            type_info = status.get("type", {})
            state = type_info.get("state")
            if state not in ("in", "pre"):
                continue  # solo en vivo y proximos (no todo el cuadro jugado)
            date = comp.get("date") or event.get("date") or ""
            if state == "pre" and date:
                # descartar muy futuros (ronda dentro de >2 dias)
                try:
                    if datetime.fromisoformat(date.replace("Z", "+00:00")).strftime("%Y%m%d%H%M%S") > limite:
                        continue
                except Exception:
                    pass
            home = _player(competitors[0])
            away = _player(competitors[1])
            etiqueta = f"{torneo} · {rama}" if rama else torneo
            out.append({
                "id": comp.get("id") or event.get("id"),
                "sport_path": None,
                "league_code": None,
                "league": etiqueta,
                "date": date,
                "name": f"{home['name']} vs {away['name']}",
                "state": state,
                "status": type_info.get("shortDetail", ""),
                "clock": status.get("displayClock", ""),
                "period": None,
                "home": home,
                "away": away,
                "home_linescores": [],
                "away_linescores": [],
                "odds": None,
                "summary": None,
            })
    return out


def get_sport_games(sport: str, league: str | None = None) -> dict:
    """Devuelve los partidos (en vivo, proximos y finalizados) de un deporte.

    Si sport == 'soccer' y se pasa league, filtra solo esa liga.
    Si no se pasa league, trae todas las ligas de futbol.
    """
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    cache_key = f"sport:{sport}:{league or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    path, label = SPORTS[sport]
    games = []

    try:
        if sport == "soccer":
            leagues_to_fetch = (
                {league: SOCCER_LEAGUES.get(league, league)}
                if league and league in SOCCER_LEAGUES
                else SOCCER_LEAGUES
            )
            for league_code, league_name in leagues_to_fetch.items():
                try:
                    data = _fetch_scoreboard(path, league=league_code)
                    for event in data.get("events", []):
                        games.append(_parse_event(event, league_name, league_code))
                except Exception:
                    continue  # una liga que falla no tira todo el deporte
        elif sport == "tennis":
            games = []
            # Traer circuitos ATP y WTA. El mismo torneo (ej. US Open) aparece
            # en ambos scoreboards con todas sus ramas, asi que deduplicamos por id.
            vistos = set()
            for tennis_path, tennis_label in (("tennis/atp", "Tenis ATP"), ("tennis/wta", "Tenis WTA")):
                try:
                    tdata = _fetch_scoreboard(tennis_path)
                    for g in _parse_tennis(tdata.get("events", [])):
                        if str(g.get("id")) in vistos:
                            continue
                        vistos.add(str(g.get("id")))
                        games.append(g)
                except Exception as e:
                    print(f"[Tennis] Error {tennis_path}: {e}")
        else:
            data = _fetch_scoreboard(path)
            for event in data.get("events", []):
                games.append(_parse_event(event, label, None))

        for g in games:
            g["sport_path"] = path
    except Exception as exc:
        return {"sport": sport, "label": label, "games": [], "error": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat()}

    # Ordenar: en vivo primero, luego proximos, luego finalizados
    state_order = {"in": 0, "pre": 1, "post": 2}
    games.sort(key=lambda g: (state_order.get(g["state"], 3), g.get("date") or ""))

    result = {
        "sport": sport,
        "label": label,
        "league": league,
        "live_count": sum(1 for g in games if g["state"] == "in"),
        "games": games,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(cache_key, result)
    return result


def get_available_leagues() -> dict:
    """Lista de ligas disponibles para el selector del frontend."""
    return {
        "soccer": [
            {"code": code, "name": name} for code, name in SOCCER_LEAGUES.items()
        ],
        "tennis": [
            {"code": "atp", "name": "Tenis ATP"},
            {"code": "wta", "name": "Tenis WTA"},
        ],
        "mma": [
            {"code": "ufc", "name": "MMA / UFC"},
        ],
        "nba": [],
        "mlb": [],
        "nfl": [],
    }


# ==============================
# DETALLE DE UN PARTIDO
# ==============================

def _parse_head_to_head(data: dict) -> list:
    """Extrae el historial de enfrentamientos directos (H2H).

    ESPN usa 'headToHead' en algunos deportes y 'seasonseries' en otros (MLB).
    """
    h2h = []

    # Formato headToHead (algunos deportes)
    for item in data.get("headToHead", []) or []:
        comp = (item.get("competitions") or [{}])[0]
        teams = {}
        for c in comp.get("competitors", []):
            ha = c.get("homeAway")
            teams[ha] = {
                "name": (c.get("team") or {}).get("displayName", "?"),
                "abbr": (c.get("team") or {}).get("abbreviation", ""),
                "logo": (c.get("team") or {}).get("logo"),
                "score": _parse_number(c.get("score")),
                "winner": c.get("winner"),
            }
        status = comp.get("status", {})
        h2h.append({
            "date": comp.get("date"),
            "status": status.get("type", {}).get("shortDetail", ""),
            "home": teams.get("home", {}),
            "away": teams.get("away", {}),
        })

    # Formato seasonseries (MLB y otros)
    for series in data.get("seasonseries", []) or []:
        for event in series.get("events", []) or []:
            teams = {}
            for c in event.get("competitors", []):
                ha = c.get("homeAway")
                teams[ha] = {
                    "name": (c.get("team") or {}).get("displayName", "?"),
                    "abbr": (c.get("team") or {}).get("abbreviation", ""),
                    "logo": (c.get("team") or {}).get("logo"),
                    "score": _parse_number(c.get("score")),
                    "winner": c.get("winner"),
                }
            h2h.append({
                "date": event.get("date"),
                "status": (event.get("statusType") or {}).get("shortDetail", ""),
                "home": teams.get("home", {}),
                "away": teams.get("away", {}),
            })

    return h2h


def _parse_recent_games(data: dict) -> dict:
    """Extrae los ultimos partidos de cada equipo."""
    result = {}
    for team_entry in data.get("teams", []) or []:
        team_id = str((team_entry.get("team") or {}).get("id"))
        team_name = (team_entry.get("team") or {}).get("displayName", "?")
        recent = []
        for event in team_entry.get("events", []) or []:
            comp = (event.get("competitions") or [{}])[0]
            teams = {}
            for c in comp.get("competitors", []):
                ha = c.get("homeAway")
                teams[ha] = {
                    "name": (c.get("team") or {}).get("displayName", "?"),
                    "score": _parse_number(c.get("score")),
                    "winner": c.get("winner"),
                }
            recent.append({
                "date": comp.get("date"),
                "status": comp.get("status", {}).get("type", {}).get("shortDetail", ""),
                "home": teams.get("home", {}),
                "away": teams.get("away", {}),
            })
        result[team_id] = {"name": team_name, "recent": recent[:5]}
    return result


def _parse_key_players(data: dict) -> list:
    """Extrae jugadores destacados del partido.

    ESPN usa 'keyPlayers' en algunos deportes y 'rosters' en otros (MLB).
    """
    players = []

    # Formato keyPlayers
    for kp in data.get("keyPlayers", []) or []:
        for p in kp.get("keyPlayers", []) or []:
            player = p.get("player", {})
            stats = []
            for s in p.get("statistics", []) or []:
                if s:
                    key = s.get("name") or ""
                    stats.append({
                        "name": STAT_TRANSLATIONS.get(key, s.get("displayName") or s.get("abbreviation") or key),
                        "value": s.get("displayValue", ""),
                    })
            players.append({
                "id": player.get("id"),
                "name": player.get("displayName", "?"),
                "headshot": player.get("headshot"),
                "team_id": str(p.get("teamId", "")),
                "stats": stats,
            })

    # Formato rosters (MLB): extraer jugadores con mejores stats
    for roster_entry in data.get("rosters", []) or []:
        team_id = str((roster_entry.get("team") or {}).get("id", ""))
        for player in roster_entry.get("roster", []) or []:
            athlete = player.get("athlete") or {}
            stats = []
            for s in player.get("stats", []) or []:
                if s:
                    key = s.get("name") or ""
                    stats.append({
                        "name": STAT_TRANSLATIONS.get(key, s.get("displayName") or s.get("abbreviation") or key),
                        "value": s.get("displayValue", ""),
                    })
            # Solo incluir si tiene stats relevantes
            if stats:
                players.append({
                    "id": athlete.get("id"),
                    "name": athlete.get("displayName", "?"),
                    "headshot": athlete.get("headshot"),
                    "team_id": team_id,
                    "stats": stats[:5],
                })

    return players[:8]  # Limitar a 8 jugadores


def _parse_team_stats(data: dict) -> dict:
    """Estadisticas comparadas por equipo desde boxscore."""
    def _translate(name):
        return STAT_TRANSLATIONS.get(name, name)

    def _flatten_stats(items):
        out = []
        for s in items or []:
            if not s:
                continue
            if "stats" in s:
                category = s.get("displayName") or s.get("name") or ""
                for sub in s["stats"] or []:
                    if sub:
                        key = sub.get("name") or ""
                        translated = _translate(key) if key in STAT_TRANSLATIONS else (
                            sub.get("displayName") or sub.get("shortDisplayName") or key or ""
                        )
                        prefix = f"{category} - {translated}" if category else translated
                        out.append({
                            "name": prefix,
                            "label": sub.get("displayValue", ""),
                        })
            else:
                key = s.get("name") or ""
                out.append({
                    "name": _translate(key) if key in STAT_TRANSLATIONS else (
                        s.get("displayName") or s.get("shortDisplayName") or key or s.get("abbreviation") or ""
                    ),
                    "label": s.get("displayValue", ""),
                })
        return out

    stats_by_team = {}
    for box_team in data.get("boxscore", {}).get("teams", []):
        team_id = str((box_team.get("team") or {}).get("id"))
        stats_by_team[team_id] = _flatten_stats(box_team.get("statistics"))
    return stats_by_team


def get_game_detail(sport: str, event_id: str) -> dict:
    """Estadisticas completas de un partido via /summary de ESPN.

    Devuelve marcador, estado, linescores, estadisticas por equipo,
    H2H, ultimos partidos y jugadores destacados.
    """
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    cache_key = f"detail:{sport}:{event_id}"
    cached = _cache_get(cache_key, ttl=DETAIL_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    path, label = SPORTS[sport]

    # Para MMA/UFC la API ESPN no expone /summary?event=ID (da 404).
    # El detalle del combate viene embebido en el scoreboard del dia,
    # asi que buscamos el evento por ID y reutilizamos el parser.
    # Para tenis tampoco existe /summary por evento (400/404). El detalle del
    # partido viene en el scoreboard filtrado por ?event=ID (dentro de
    # groupings). Buscamos la competencia y armamos el detalle desde ahi.
    if sport == "tennis":
        data = _fetch_scoreboard(path, params_extra={"event": event_id})
        found = None
        for ev in data.get("events", []):
            for g in ev.get("groupings") or []:
                for comp in g.get("competitions") or []:
                    if str(comp.get("id")) == str(event_id):
                        found = (ev, g.get("grouping", {}).get("displayName", ""), comp)
                        break
            if found:
                break
        if not found:
            return {"error": "Partido de tenis no encontrado", "games": []}
        ev, rama, comp = found
        torneo = ev.get("shortName") or ev.get("name") or "Tenis"

        def _tplayer(competitor: dict) -> dict:
            ath = competitor.get("athlete") or {}
            name = ath.get("displayName") or "?"
            flag = ath.get("flag") or ath.get("logo")
            if isinstance(flag, list):
                flag = flag[0] if flag else None
            return {
                "id": ath.get("id"),
                "name": name,
                "short_name": ath.get("shortName") or name,
                "abbr": ath.get("shortName") or "",
                "logo": flag,
                "color": None,
                "alt_color": None,
                "score": _parse_number(competitor.get("score")),
                "winner": competitor.get("winner"),
                "linescores": [
                    {"value": _parse_number(p.get("value")), "displayValue": p.get("displayValue", "")}
                    for p in competitor.get("linescores") or []
                ],
                "records": [r.get("summary") for r in competitor.get("records") or [] if r],
                "statistics": [
                    {"name": s.get("name"), "displayValue": s.get("displayValue")}
                    for s in competitor.get("statistics") or []
                ],
            }

        competitors = comp.get("competitors") or []
        status = comp.get("status") or ev.get("status") or {}
        type_info = status.get("type", {})
        teams_out = [_tplayer(competitors[0]), _tplayer(competitors[1])]
        teams_out[0]["homeAway"] = "home"
        teams_out[1]["homeAway"] = "away"
        result = {
            "sport": sport,
            "label": f"{torneo} · {rama}" if rama else torneo,
            "event_id": event_id,
            "league": f"{torneo} · {rama}" if rama else torneo,
            "state": type_info.get("state"),
            "status": type_info.get("shortDetail", ""),
            "clock": status.get("displayClock", ""),
            "period": None,
            "situation": None,
            "teams": teams_out,
            "head_to_head": [],
            "key_players": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _cache_set(cache_key, result)
        return result

    if sport == "mma":
        data = _fetch_scoreboard("mma/ufc")
        for event in data.get("events", []):
            if str(event.get("id")) == str(event_id):
                parsed = _parse_event(event, label, None)
                # El endpoint /stats/ai-analysis consume detail["teams"] con
                # {homeAway, name, score, records, linescores}. Normalizamos
                # el resultado de MMA (que usa home/away) al formato esperado.
                teams = []
                for side in ("home", "away"):
                    t = parsed.get(side) or {}
                    teams.append({
                        "homeAway": "home" if side == "home" else "away",
                        "id": t.get("id"),
                        "name": t.get("name", "?"),
                        "short_name": t.get("short_name", t.get("name", "?")),
                        "abbr": t.get("abbr", ""),
                        "logo": t.get("logo"),
                        "score": t.get("score"),
                        "records": [t.get("record")] if t.get("record") else [],
                        "linescores": t.get("home_linescores") if side == "home" else t.get("away_linescores", []),
                        "color": t.get("color"),
                        "alt_color": t.get("alt_color"),
                    })
                parsed["teams"] = teams
                return parsed
        return {"error": "Combate no encontrado", "games": []}

    # Para soccer necesitamos la liga; la buscamos en el cache del scoreboard
    league_code = None
    if sport == "soccer":
        games_data = get_sport_games(sport)
        for g in games_data.get("games", []):
            if str(g.get("id")) == str(event_id):
                league_code = g.get("league_code")
                break
        if not league_code:
            return {"error": "Partido no encontrado", "games": []}

    url = (
        f"{ESPN_BASE}/{path}/{league_code}/summary?event={event_id}"
        if league_code
        else f"{ESPN_BASE}/{path}/summary?event={event_id}"
    )
    data = _fetch_json(url)

    header = data.get("header", {})
    comps = header.get("competitions") or [{}]
    comp = comps[0] if comps else {}
    status = comp.get("status", {})
    type_info = status.get("type", {})

    teams_out = []
    for competitor in comp.get("competitors", []):
        team = competitor.get("team", {})
        teams_out.append({
            "id": team.get("id"),
            "name": team.get("displayName", "?"),
            "short_name": team.get("shortDisplayName", team.get("name", "?")),
            "abbr": team.get("abbreviation", ""),
            "logo": (team.get("logo") or [None])[0] if isinstance(team.get("logo"), list) else team.get("logo"),
            "color": f"#{team.get('color')}" if team.get("color") else None,
            "alt_color": f"#{team.get('alternateColor')}" if team.get("alternateColor") else None,
            "score": _parse_number(competitor.get("score")),
            "winner": competitor.get("winner"),
            "homeAway": competitor.get("homeAway"),
            "linescores": _linescores(competitor),
            "records": [r.get("summary") for r in (competitor.get("records") or []) if r],
        })

    # Estadisticas por equipo
    stats_by_team = _parse_team_stats(data)
    for t in teams_out:
        t["statistics"] = stats_by_team.get(str(t["id"]), [])

    # Situacion en vivo (MLB): inning, outs, bases
    situation = None
    sit = comp.get("situation")
    if sit:
        situation = {
            "isTop": sit.get("isTop"),
            "inning": sit.get("inning"),
            "outs": sit.get("outs"),
            "onFirst": bool(sit.get("onFirst")),
            "onSecond": bool(sit.get("onSecond")),
            "onThird": bool(sit.get("onThird")),
            "strikes": sit.get("strikes"),
            "balls": sit.get("balls"),
        }

    # H2H, ultimos partidos, jugadores destacados
    h2h = _parse_head_to_head(data)
    recent = _parse_recent_games(data)
    key_players = _parse_key_players(data)

    # Asignar ultimos partidos a cada equipo
    for t in teams_out:
        t["recent_games"] = recent.get(str(t["id"]), {}).get("recent", [])

    # Backfill: si el summary no trae ultimos partidos (pasa en futbol),
    # buscarlos via scoreboard por rango de fechas.
    for t in teams_out:
        if t.get("recent_games"):
            continue
        try:
            evs = get_team_recent_events(sport, league_code, t["id"], limit=5)
            t["recent_games"] = _recent_from_events(evs)[:5]
        except Exception as e:
            print(f"[Detail] recent backfill team={t.get('id')}: {e}")

    result = {
        "sport": sport,
        "label": label,
        "event_id": event_id,
        "league": (SOCCER_LEAGUES.get(league_code) if league_code else label),
        "state": type_info.get("state"),
        "status": type_info.get("shortDetail", ""),
        "clock": status.get("displayClock", ""),
        "period": status.get("period"),
        "situation": situation,
        "teams": teams_out,
        "head_to_head": h2h,
        "key_players": key_players,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(cache_key, result)
    return result


def get_all_sports_summary() -> dict:
    """Resumen de todos los deportes (para la vista general de Estadisticas)."""
    summary = {"sports": [], "updated_at": datetime.now(timezone.utc).isoformat()}
    for sport in SPORTS:
        data = get_sport_games(sport)
        summary["sports"].append({
            "sport": data["sport"],
            "label": data["label"],
            "live_count": data.get("live_count", 0),
            "total": len(data.get("games", [])),
            "error": data.get("error"),
        })
    return summary