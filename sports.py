"""
Modulo de estadisticas deportivas en vivo usando la API publica de ESPN.
Cubre: futbol (soccer), NBA, MLB, NFL y tenis.

Sin API key, sin costo. Cache en memoria con TTL para no golpear la API
mas de lo necesario (ESPN refresca sus marcadores cada ~15s).
"""

import time
import threading
from datetime import datetime, timedelta, timezone

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
    "uefa.nations": "UEFA Nations League",
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
    # NBA: claves que ESPN manda EN EL BOX SCORE de un partido (no coinciden con
    # las de arriba y antes se pintaban tal cual en ingles: "totalRebounds").
    "totalRebounds": "Rebotes",
    "fouls": "Faltas",
    "pointsInPaint": "Puntos en la pintura",
    "turnoverPoints": "Puntos tras perdida",
    "pointsOffTurnovers": "Puntos tras perdida",
    "teamTurnovers": "Perdidas de equipo",
    "totalTurnovers": "Perdidas totales",
    "technicalFouls": "Faltas tecnicas",
    "totalTechnicalFouls": "Faltas tecnicas (totales)",
    "flagrantFouls": "Faltas flagrantes",
    "largestLead": "Mayor ventaja",
    "leadPercentage": "Tiempo por delante %",
    # NBA: medias de temporada que ESPN manda cuando el partido aun no empezo
    "avgPoints": "Puntos por partido",
    "avgPointsAgainst": "Puntos recibidos por partido",
    "avgRebounds": "Rebotes por partido",
    "avgAssists": "Asistencias por partido",
    "avgBlocks": "Tapones por partido",
    "avgSteals": "Robos por partido",
    "avgTurnovers": "Perdidas por partido",
    "avgTeamTurnovers": "Perdidas de equipo por partido",
    "avgTotalTurnovers": "Perdidas totales por partido",
    "streak": "Racha",
    "Last Ten Games": "Ultimos 10 partidos (V-D)",
    "gamesPlayed": "Partidos jugados",
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


def _dias_rango() -> list:
    """Fechas individuales (uno por dia) de ayer..pasado-manana, hora del Este.

    ESPN agrupa eventos por fecha del Este (America/New_York), no UTC.
    Un partido del 2026-10-21T01:30Z es en realidad 2026-10-20T20:30 ET,
    así que pertenece al día 20 en ESPN. Si usamos UTC, buscamos en el día
    21 y no lo encontramos (NOT_FOUND). Por eso convertimos a Eastern Time.

    ESPN YA NO ACEPTA rangos en dates=YYYYMMDD-YYYYMMDD (responde 400 Bad
    Request desde sep/2026). Solo acepta UNA fecha por peticion, por eso
    devolvemos una lista de dias y _fetch_scoreboard hace una peticion por dia.
    """
    from datetime import timedelta
    today_utc = datetime.now(timezone.utc)
    
    # Convertir UTC a Eastern Time (America/New_York)
    # Intentamos zoneinfo primero (disponible en Linux y con tzdata en Windows)
    try:
        from zoneinfo import ZoneInfo
        tz_eastern = ZoneInfo("America/New_York")
        today_eastern = today_utc.astimezone(tz_eastern)
    except (ImportError, Exception):
        # Fallback: calcular Eastern Time manualmente considerando DST
        # DST en US: segundo domingo de marzo a primer domingo de noviembre
        year = today_utc.year
        
        # Segundo domingo de marzo
        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
        mar1_weekday = mar1.weekday()  # 0=lunes, 6=domingo
        second_sunday_mar = 8 + (6 - mar1_weekday) if mar1_weekday != 6 else 14
        
        # Primer domingo de noviembre
        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
        nov1_weekday = nov1.weekday()
        first_sunday_nov = 1 + (6 - nov1_weekday) if nov1_weekday != 6 else 7
        
        dst_start = datetime(year, 3, second_sunday_mar, 7, 0, 0, tzinfo=timezone.utc)  # 2 AM EST = 7 AM UTC
        dst_end = datetime(year, 11, first_sunday_nov, 6, 0, 0, tzinfo=timezone.utc)  # 2 AM EDT = 6 AM UTC
        
        # Si estamos en DST, UTC-4; si no, UTC-5
        offset_hours = -4 if dst_start <= today_utc < dst_end else -5
        tz_eastern = timezone(timedelta(hours=offset_hours))
        today_eastern = today_utc.astimezone(tz_eastern)
    
    return [
        (today_eastern - timedelta(days=1)).strftime("%Y%m%d"),
        today_eastern.strftime("%Y%m%d"),
        (today_eastern + timedelta(days=1)).strftime("%Y%m%d"),
        (today_eastern + timedelta(days=2)).strftime("%Y%m%d"),
    ]


TZ_NIC = timezone(timedelta(hours=-6), "America/Managua")

_DAY_LABELS = {-1: "Ayer", 0: "Hoy", 1: "Mañana", 2: "Pasado mañana"}


def _day_label(date_str: str | None) -> str | None:
    """Etiqueta del dia del partido en hora Nicaragua: Hoy/Mañana/Pasado mañana/Ayer."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        local = dt.astimezone(TZ_NIC)
        hoy = datetime.now(timezone.utc).astimezone(TZ_NIC).date()
        delta = (local.date() - hoy).days
        if delta in _DAY_LABELS:
            return _DAY_LABELS[delta]
        return local.strftime("%d/%m")
    except (ValueError, TypeError):
        return None



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
    # Detalle de un partido puntual: SIN dates (con dates= un solo dia ya
    # funciona, pero es innecesario; con rango daba 400 y rompia la resolucion
    # de picks).
    if params_extra and "event" in params_extra:
        resp = _espn_get(url, params=params_extra)
        return resp.json()
    # El tenis anida los torneos en un solo evento; si filtramos por fechas
    # perdemos los torneos en curso (ej. US Open). Sin dates trae todo.
    if path.startswith("tennis"):
        resp = _espn_get(url)
        return resp.json()
    # ESPN ya no acepta rangos (dates=A-B -> 400 Bad Request): una peticion
    # por dia (en paralelo) y fusionamos los eventos (deduplicados por id).
    from concurrent.futures import ThreadPoolExecutor

    def _get_dia(dia: str):
        params = {"dates": dia}
        if params_extra:
            params.update(params_extra)
        try:
            return _espn_get(url, params=params).json()
        except Exception:
            return None  # un dia que falla no tira el resto

    dias = _dias_rango()
    with ThreadPoolExecutor(max_workers=4) as ex:
        resultados = list(ex.map(_get_dia, dias))

    data = None
    vistos = set()
    for djson in resultados:
        if not djson:
            continue
        if data is None:
            data = djson
            data["events"] = []
        for ev in djson.get("events", []):
            eid = str(ev.get("id") or "")
            if eid and eid in vistos:
                continue
            if eid:
                vistos.add(eid)
            data["events"].append(ev)
    if data is None:
        raise Exception(f"ESPN sin respuesta para {path} en {len(dias)} dias")
    return data


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


def get_team_schedule_results(sport: str, league: str | None, team_id, limit: int = 10) -> list:
    """Ultimos partidos finalizados de un equipo via /teams/{id}/schedule.

    Devuelve la temporada completa del equipo en su competicion (los pasados
    con marcador), normalizada como [{away: {name, score}, home: {...}}, ...]
    ordenada de mas antigua a mas reciente. Lista vacia si no hay datos.
    """
    path, _label = SPORTS[sport]
    if not team_id:
        return []
    es_soccer = path.startswith("soccer")
    if es_soccer and not league:
        return []
    url = (
        f"{ESPN_BASE}/{path}/{league}/teams/{team_id}/schedule"
        if es_soccer
        else f"{ESPN_BASE}/{path}/teams/{team_id}/schedule"
    )
    cache_key = f"team_sched:{sport}:{league or ''}:{team_id}:{limit}"
    cached = _cache_get(cache_key, ttl=1800)
    if cached is not None:
        return cached

    try:
        resp = _espn_get(url, timeout=12)
        data = resp.json()
    except Exception:
        _cache_set(cache_key, [])
        return []

    def _score(valor):
        if isinstance(valor, dict):
            valor = valor.get("value")
        if isinstance(valor, list):
            valor = valor[0].get("value") if valor else None
        return _parse_number(valor)

    eventos = []
    for ev in data.get("events") or []:
        comp0 = (ev.get("competitions") or [{}])[0]
        estado = ((comp0.get("status") or {}).get("type") or {}).get("state", "")
        if estado != "post":
            continue
        norm = {"id": ev.get("id"), "date": ev.get("date", "")}
        for c in comp0.get("competitors", []):
            lado = c.get("homeAway")
            if lado not in ("home", "away"):
                continue
            norm[lado] = {
                "name": (c.get("team") or {}).get("displayName", "?"),
                "score": _score(c.get("score")),
            }
        if "home" in norm and "away" in norm:
            eventos.append(norm)

    eventos.sort(key=lambda e: e.get("date", ""))
    eventos = eventos[-limit:]
    _cache_set(cache_key, eventos)
    return eventos


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
    # ESPN ya no acepta rangos (dates=A-B -> 400): una peticion por dia en
    # paralelo, ultimos 21 dias (suficiente para 6 partidos finalizados).
    from datetime import timedelta
    from concurrent.futures import ThreadPoolExecutor
    dias = [
        (today - timedelta(days=d)).strftime("%Y%m%d")
        for d in range(1, 22)
    ]
    url = (
        f"{ESPN_BASE}/{path}/{league}/scoreboard"
        if (league and path.startswith("soccer"))
        else f"{ESPN_BASE}/{path}/scoreboard"
    )

    def _get_dia(dia: str):
        try:
            return _espn_get(url, params={"dates": dia}).json()
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        resultados = list(ex.map(_get_dia, dias))

    data_events = []
    vistos = set()
    for djson in resultados:
        if not djson:
            continue
        for ev in djson.get("events", []):
            eid = str(ev.get("id") or "")
            if eid and eid in vistos:
                continue
            if eid:
                vistos.add(eid)
            data_events.append(ev)

    events = []
    for ev in data_events:
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

    def _extraer_url(valor):
        """Extrae la URL real de un campo de imagen de ESPN.

        ESPN devuelve 'headshot' y 'flag' como objetos {'href': url} (o listas
        de ellos). Guardar el dict tal cual produce URLs invalidas y el
        frontend muestra la imagen rota. Aqui siempre devolvemos un string
        http(s) o None.
        """
        if isinstance(valor, dict):
            valor = valor.get("href")
        elif isinstance(valor, list):
            primero = valor[0] if valor else None
            valor = primero.get("href") if isinstance(primero, dict) else primero
        if isinstance(valor, str) and valor.startswith("http"):
            return valor
        return None

    def _player(competitor: dict) -> dict:
        # ESPN usa `athlete` en individuales y `roster` en dobles.
        # Antes solo se leia athlete, por eso los dobles aparecian como "?".
        ath = competitor.get("athlete") or {}
        roster = competitor.get("roster") or {}
        athletes = roster.get("athletes") or ([ath] if ath else [])
        name = (
            roster.get("displayName")
            or ath.get("displayName")
            or ath.get("fullName")
            or (roster.get("athletes") or [{}])[0].get("displayName")
            or "?"
        )
        imagen = None
        for player in athletes:
            imagen = (
                _extraer_url(player.get("headshot"))
                or _extraer_url(player.get("flag"))
                or _extraer_url(player.get("logo"))
            )
            if imagen:
                break
        first = athletes[0] if athletes else {}
        return {
            "id": roster.get("id") or ath.get("id") or first.get("id"),
            "name": name,
            "short_name": roster.get("shortDisplayName") or ath.get("shortName") or name,
            "abbr": roster.get("shortDisplayName") or ath.get("shortName") or "",
            "logo": imagen,
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


def get_standings(league: str) -> dict:
    """Tabla de posiciones de una liga de futbol (standings de ESPN).

    Devuelve grupos (por nota de clasificacion: Champions League, Europa
    League, etc. o "General") con equipos: PJ, G, E, P, DIF, GF, GC, PTS,
    nota de zona y forma de los ultimos 5 (W/D/L) calculada del scoreboard
    de las ultimas 3 semanas.
    """
    league = (league or "").strip()
    if league not in SOCCER_LEAGUES:
        raise ValueError(f"Liga no soportada: {league}")

    cache_key = f"standings:{league}"
    cached = _cache_get(cache_key, ttl=1800)  # 30 min: las tablas no cambian rapido
    if cached is not None:
        return cached

    base = (
        ESPN_HOSTS[_espn_host_idx["i"] % len(ESPN_HOSTS)]
        .replace("apis/site/v2/sports", "apis/v2/sports")
    )
    data = _espn_get(f"{base}/soccer/{league}/standings", timeout=15).json()

    def _stats_map(stats: list) -> dict:
        out = {}
        for s in stats or []:
            nombre = s.get("name")
            valor = s.get("value")
            if nombre and valor is not None:
                out[nombre] = valor
        return out

    def _num(m: dict, *nombres, default=None):
        for n in nombres:
            if n in m:
                try:
                    return float(m[n])
                except (TypeError, ValueError):
                    continue
        return default

    # Forma: ultimos resultados del scoreboard de las ultimas 3 semanas
    forma_por_equipo: dict = {}
    try:
        from datetime import datetime, timedelta, timezone
        hoy = datetime.now(timezone.utc).date()
        desde = (hoy - timedelta(days=21)).strftime("%Y%m%d")
        hasta = hoy.strftime("%Y%m%d")
        sb = None
        ultimo_exc = None
        for _intento in range(2):
            try:
                sb = _espn_get(
                    f"{ESPN_HOSTS[_espn_host_idx['i'] % len(ESPN_HOSTS)]}/soccer/{league}/scoreboard",
                    params={"dates": f"{desde}-{hasta}"},
                    timeout=15,
                ).json()
                break
            except Exception as exc:
                ultimo_exc = exc
                import time as _time
                _time.sleep(2)
        if sb is None:
            raise ultimo_exc or Exception("scoreboard sin respuesta")
        resultados: dict = {}
        for ev in sb.get("events") or []:
            try:
                comp = (ev.get("competitions") or [{}])[0]
                comps = comp.get("competitors") or []
                if len(comps) < 2:
                    continue
                c1, c2 = comps[0], comps[1]
                s1, s2 = _parse_number(c1.get("score")), _parse_number(c2.get("score"))
                # Solo partidos FINALIZADOS (con marcador y estado post; si el
                # estado no viene, los marcadores de fechas pasadas cuentan).
                estado = (comp.get("status") or {}).get("type", {}).get("state")
                if s1 is None or s2 is None or (estado and estado != "post"):
                    continue
                r1 = "W" if s1 > s2 else ("D" if s1 == s2 else "L")
                r2 = "W" if s2 > s1 else ("D" if s1 == s2 else "L")
                resultados.setdefault(str(c1.get("team", {}).get("id")), []).append(r1)
                resultados.setdefault(str(c2.get("team", {}).get("id")), []).append(r2)
            except Exception:
                continue
        for tid, lista in resultados.items():
            forma_por_equipo[tid] = lista[-5:]
    except Exception as exc:
        print(f"[Standings] forma no disponible para {league}: {exc!r}", flush=True)

    grupos = []
    for child in data.get("children") or []:
        st = child.get("standings") or {}
        entries = st.get("entries") or []
        filas = []
        for e in entries:
            tm = e.get("team") or {}
            m = _stats_map(e.get("stats"))
            pj = int(_num(m, "gamesPlayed") or 0)
            g = int(_num(m, "wins") or 0)
            e_ = int(_num(m, "ties") or 0)
            p = int(_num(m, "losses") or 0)
            pts = int(_num(m, "points") or 0)
            gf = _num(m, "pointsFor", "goalsFor") or 0
            gc = _num(m, "pointsAgainst", "goalsAgainst") or 0
            dif = _num(m, "differential", "goalDifference")
            if dif is None:
                dif = gf - gc
            gf, gc, dif = int(gf), int(gc), int(dif)
            nota = ((e.get("note") or {}).get("description")
                    or (e.get("note") or {}).get("color")
                    or "")
            logos = tm.get("logos") or []
            logo = logos[0].get("href") if logos else (tm.get("logo") or None)
            filas.append({
                "team": tm.get("displayName") or "?",
                "abbr": tm.get("abbreviation") or "",
                "logo": logo,
                "pj": pj, "g": g, "e": e_, "p": p,
                "dif": dif, "gf": gf, "gc": gc,
                "pts": pts,
                "nota": nota,
                "forma": forma_por_equipo.get(str(tm.get("id")), []),
            })
        if filas:
            grupos.append({
                "nombre": child.get("name") or child.get("displayName") or "General",
                "equipos": filas,
            })

    respuesta = {
        "league": league,
        "liga": SOCCER_LEAGUES.get(league, league),
        "grupos": grupos,
    }
    from datetime import datetime as _dt, timezone as _tz
    respuesta["actualizado"] = _dt.now(_tz.utc).isoformat()
    _cache_set(cache_key, respuesta)
    return respuesta

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
            g["dayLabel"] = _day_label(g.get("date"))
    except Exception as exc:
        return {"sport": sport, "label": label, "games": [], "error": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat()}

    # Deduplicar (el rango de fechas puede repetir eventos entre dias)
    unicos = []
    vistos_ids = set()
    for g in games:
        gid = str(g.get("id") or "")
        if gid and gid in vistos_ids:
            continue
        if gid:
            vistos_ids.add(gid)
        unicos.append(g)
    games = unicos

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


# ESPN manda tiros y porcentajes como claves separadas:
#   "fieldGoalsMade-fieldGoalsAttempted" = "44-96"   (anotados-intentados)
#   "fieldGoalPct"                       = "46"      (entero, redondeado)
# Aqui se agrupan para poder pintar "Tiros de campo" = "44-96" y recalcular el
# porcentaje EXACTO desde los tiros (44/96 = 45.8%), en vez de usar el 46 de ESPN.
_STATS_COMBINADAS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("Tiros de campo", "fieldGoalPct"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("Triples", "threePointFieldGoalPct"),
    "freeThrowsMade-freeThrowsAttempted": ("Tiros libres", "freeThrowPct"),
}


def _numero(texto):
    """Convierte "45.5", "1,234" o "45.5%" en float (None si no se puede)."""
    if texto is None:
        return None
    limpio = str(texto).replace("%", "").replace(",", "").strip()
    try:
        return float(limpio)
    except ValueError:
        return None


def _porcentaje(anotados, intentados):
    """"44-96" -> 45.8 (un decimal, sin inventar)."""
    if not intentados:
        return None
    return round(anotados * 100 / intentados, 1)


def _texto_racha(valor):
    """ESPN manda "W5"/"L2": en español V5/D2."""
    texto = str(valor or "")
    if len(texto) < 2:
        return texto
    letra, resto = texto[0].upper(), texto[1:]
    if letra == "W":
        return f"V{resto}"
    if letra == "L":
        return f"D{resto}"
    return texto


def _parse_team_stats(data: dict) -> dict:
    """Estadisticas comparadas por equipo desde boxscore.

    Devuelve por equipo [{name, label, value}]:
    - name  : etiqueta traducida al español (o la de ESPN si no hay traduccion)
    - label : valor listo para pintar ("44-96", "45.8%", "51")
    - value : numero (para barras y para la IA); None en los valores combinados
    """
    def _translate(name):
        return STAT_TRANSLATIONS.get(name, name)

    def _etiqueta(key, item):
        return _translate(key) if key in STAT_TRANSLATIONS else (
            item.get("displayName") or item.get("shortDisplayName") or key or item.get("abbreviation") or ""
        )

    def _flatten_stats(items):
        out = []
        conteos = {}  # clave_pct -> (anotados, intentados)

        for s in items or []:
            if not s:
                continue
            if "stats" in s:
                # Estadisticas agrupadas por categoria (box score de futbol, etc.)
                category = s.get("displayName") or s.get("name") or ""
                for sub in s["stats"] or []:
                    if not sub:
                        continue
                    key = sub.get("name") or ""
                    translated = _etiqueta(key, sub)
                    prefix = f"{category} - {translated}" if category else translated
                    out.append({
                        "name": prefix,
                        "label": sub.get("displayValue", ""),
                        "value": _numero(sub.get("value") if sub.get("value") is not None else sub.get("displayValue")),
                    })
                continue

            key = s.get("name") or ""
            valor = s.get("displayValue", "")

            # "44-96" -> fila legible + conteo para recalcular el porcentaje
            if key in _STATS_COMBINADAS:
                titulo, clave_pct = _STATS_COMBINADAS[key]
                partes = str(valor).split("-")
                if len(partes) == 2:
                    anotados, intentados = _numero(partes[0]), _numero(partes[1])
                    if anotados is not None and intentados is not None:
                        conteos[clave_pct] = (anotados, intentados)
                out.append({
                    "name": f"{titulo} (anotados-intentados)",
                    "label": valor,
                    "value": None,
                })
                continue

            out.append({
                "name": _etiqueta(key, s),
                "label": _texto_racha(valor) if key == "streak" else valor,
                "value": _numero(s.get("value") if s.get("value") is not None else valor),
            })

        # Porcentajes recalculados con los tiros reales (mas preciso que ESPN)
        for fila in out:
            clave = None
            for nombre_pct in ("fieldGoalPct", "threePointFieldGoalPct", "freeThrowPct"):
                if fila["name"] == STAT_TRANSLATIONS.get(nombre_pct, nombre_pct) and nombre_pct in conteos:
                    clave = nombre_pct
                    break
            if not clave:
                continue
            anotados, intentados = conteos[clave]
            exacto = _porcentaje(anotados, intentados)
            if exacto is not None:
                fila["label"] = f"{exacto}%"
                fila["value"] = exacto

        return out

    stats_by_team = {}
    for box_team in data.get("boxscore", {}).get("teams", []):
        team_id = str((box_team.get("team") or {}).get("id"))
        stats_by_team[team_id] = _flatten_stats(box_team.get("statistics"))
    return stats_by_team


def _logo_equipo(team: dict):
    """URL del escudo. ESPN lo manda en `logos[]` (no en `logo`)."""
    logos = team.get("logos") or []
    if isinstance(logos, list) and logos:
        primero = logos[0]
        if isinstance(primero, dict):
            return primero.get("href")
        if isinstance(primero, str):
            return primero
    logo = team.get("logo")
    if isinstance(logo, list):
        return logo[0] if logo else None
    return logo


def _parse_lineups(data: dict) -> list:
    """Alineaciones desde `rosters` del summary de ESPN.

    Devuelve una entrada por equipo:
      {equipo, equipo_id, lado, formacion, titulares[], suplentes[]}
    Cada jugador: {id, nombre, dorsal, pos, orden, foto, goles, asistencias,
    amarillas, rojas, entra, sale}. `orden` es el formationPlace de ESPN
    (1 = portero) y los goles/tarjetas salen de `stats` del roster.
    """
    def _num(valor):
        try:
            return int(str(valor).strip())
        except (TypeError, ValueError):
            return 99

    def _stat(stats, nombre):
        for s in stats or []:
            if s.get("name") == nombre:
                try:
                    return int(float(s.get("value") or 0))
                except (TypeError, ValueError):
                    return 0
        return 0

    salida = []
    for entrada in data.get("rosters") or []:
        equipo = entrada.get("team") or {}
        titulares, suplentes = [], []
        for p in entrada.get("roster") or []:
            ath = p.get("athlete") or {}
            foto = ath.get("headshot")
            if isinstance(foto, dict):
                foto = foto.get("href")
            stats = p.get("stats") or []
            jugador = {
                "id": ath.get("id"),
                "nombre": ath.get("shortName") or ath.get("displayName") or "?",
                "dorsal": p.get("jersey"),
                "pos": ((p.get("position") or {}).get("abbreviation")
                        or (p.get("position") or {}).get("name") or ""),
                "orden": _num(p.get("formationPlace")),
                "foto": foto,
                "goles": _stat(stats, "totalGoals"),
                "asistencias": _stat(stats, "goalAssists"),
                "amarillas": _stat(stats, "yellowCards"),
                "rojas": _stat(stats, "redCards"),
                "entra": bool(p.get("subbedIn")),
                "sale": bool(p.get("subbedOut")),
            }
            (titulares if p.get("starter") else suplentes).append(jugador)

        titulares.sort(key=lambda j: j.get("orden") or 99)
        salida.append({
            "equipo": equipo.get("displayName") or equipo.get("name") or "",
            "equipo_id": equipo.get("id"),
            "lado": entrada.get("homeAway"),
            "formacion": entrada.get("formation") or "",
            "titulares": titulares,
            "suplentes": suplentes,
        })
    return salida


# ---------------------------------------------------------------------------
# ANALYTICS NBA: cuatro factores, ritmo/eficiencia, caja de jugadores y rachas
# ---------------------------------------------------------------------------
# Todo se calcula desde el box score y los plays de ESPN (nada inventado).
# Formulas: Dean Oliver (4 factores), possessions = FGA + 0.44*FTA - OREB + TOV,
# OffRtg = 100*PTS/poss, Pace = poss*48/minutos.

_STATS_JUGADOR = {
    "minutes": "min",
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
    "turnovers": "to",
    "steals": "stl",
    "blocks": "blk",
    "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb",
    "fouls": "pf",
    "plusMinus": "plus",
}

_COMBINADAS_JUGADOR = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}

# Mismas claves combinadas, pero para los TOTALES del equipo
_COMBINADAS_EQUIPO = dict(_COMBINADAS_JUGADOR)


def _porcentaje_seguro(parte, total, decimales=1):
    if not total:
        return None
    return round(parte * 100 / total, decimales)


def _minutos_jugados(state: str, period, clock: str) -> float:
    """Minutos que se llevan jugados (48 por partido, 5 por prorroga)."""
    try:
        periodo = int(period or 0)
    except (TypeError, ValueError):
        periodo = 0

    if state == "post":
        if periodo <= 4:
            return 12.0 * max(periodo, 4)
        return 48.0 + 5.0 * (periodo - 4)

    if not periodo:
        return 0.0
    restante = 0.0
    texto = str(clock or "").strip()
    if ":" in texto:
        mm, _, ss = texto.partition(":")
        try:
            restante = int(mm) + int(ss) / 60
        except ValueError:
            restante = 0.0
    if periodo <= 4:
        return 12.0 * (periodo - 1) + max(0.0, 12.0 - restante)
    return 48.0 + 5.0 * (periodo - 5) + max(0.0, 5.0 - restante)


def _totales_equipo(box_team: dict) -> dict:
    """Totales numericos del equipo (a partir de las mismas filas que se pintan)."""
    totales = {}
    for s in box_team.get("statistics") or []:
        clave = s.get("name") or ""
        valor = s.get("displayValue")
        if clave in _COMBINADAS_EQUIPO:
            a, b = _COMBINADAS_EQUIPO[clave]
            partes = str(valor or "").split("-")
            if len(partes) == 2:
                totales[a] = _numero(partes[0]) or 0.0
                totales[b] = _numero(partes[1]) or 0.0
            continue
        totales[clave] = _numero(valor)
    return totales


def _analytics_equipo(totales: dict, totales_rival: dict, minutos: float) -> dict:
    """Cuatro factores + ritmo y eficiencia (estilo Cleaning the Glass)."""
    fga = totales.get("fga") or 0
    fgm = totales.get("fgm") or 0
    fg3m = totales.get("fg3m") or 0
    fta = totales.get("fta") or 0
    ftm = totales.get("ftm") or 0
    oreb = totales.get("offensiveRebounds") or 0
    dreb_rival = totales_rival.get("defensiveRebounds") or 0
    tov = totales.get("totalTurnovers") or totales.get("turnovers") or 0
    pts = totales.get("points")
    if pts is None:
        pts = 2 * fgm + fg3m + ftm

    posesiones = fga + 0.44 * fta - oreb + tov
    ortg = round(100 * pts / posesiones, 1) if posesiones else None
    pace = round(posesiones * 48 / minutos, 1) if minutos and posesiones else None

    return {
        "posesiones": round(posesiones, 1) if posesiones else None,
        "ortg": ortg,
        "pace": pace,
        "efg": _porcentaje_seguro(fgm + 0.5 * fg3m, fga),
        "tov_pct": _porcentaje_seguro(tov, fga + 0.44 * fta + tov),
        "oreb_pct": _porcentaje_seguro(oreb, oreb + dreb_rival),
        "ftr": _porcentaje_seguro(fta, fga),
        "ts": _porcentaje_seguro(pts, 2 * (fga + 0.44 * fta)),
        "puntos": pts,
        "minutos": round(minutos, 1),
    }


def _parse_players_box(data: dict) -> list:
    """Caja completa de jugadores (15 por equipo) con metricas derivadas."""
    salida = []
    for bloque in (data.get("boxscore") or {}).get("players") or []:
        equipo = bloque.get("team") or {}
        for est in bloque.get("statistics") or []:
            claves = list(est.get("keys") or [])
            jugadores = []
            for fila in est.get("athletes") or []:
                atleta = fila.get("athlete") or {}
                valores = fila.get("stats") or []
                if not atleta.get("displayName") or fila.get("didNotPlay"):
                    continue

                crudos = dict(zip(claves, valores))
                jugador = {
                    "id": atleta.get("id"),
                    "nombre": atleta.get("displayName"),
                    "corto": atleta.get("shortName") or atleta.get("displayName"),
                    "foto": (atleta.get("headshot") or {}).get("href"),
                    "dorsal": atleta.get("jersey"),
                    "posicion": (atleta.get("position") or {}).get("abbreviation") or "",
                    "titular": bool(fila.get("starter")),
                }

                for clave_espn, clave in _STATS_JUGADOR.items():
                    jugador[clave] = _numero(crudos.get(clave_espn))

                for clave_espn, (a, b) in _COMBINADAS_JUGADOR.items():
                    partes = str(crudos.get(clave_espn) or "").split("-")
                    if len(partes) == 2:
                        jugador[a] = _numero(partes[0]) or 0
                        jugador[b] = _numero(partes[1]) or 0
                    else:
                        jugador[a], jugador[b] = 0, 0

                fga = jugador.get("fga") or 0
                fta = jugador.get("fta") or 0
                pts = jugador.get("pts") or 0
                jugador["ts"] = _porcentaje_seguro(pts, 2 * (fga + 0.44 * fta)) if (fga or fta) else None
                jugador["efg"] = (
                    _porcentaje_seguro((jugador.get("fgm") or 0) + 0.5 * (jugador.get("fg3m") or 0), fga)
                    if fga
                    else None
                )
                jugador["game_score"] = round(
                    pts
                    + 0.4 * (jugador.get("fgm") or 0)
                    - 0.7 * fga
                    - 0.4 * (fta - (jugador.get("ftm") or 0))
                    + 0.7 * (jugador.get("oreb") or 0)
                    + 0.3 * (jugador.get("dreb") or 0)
                    + (jugador.get("stl") or 0)
                    + 0.7 * (jugador.get("ast") or 0)
                    + 0.7 * (jugador.get("blk") or 0)
                    - 0.4 * (jugador.get("pf") or 0)
                    - (jugador.get("to") or 0),
                    1,
                )
                jugadores.append(jugador)

            jugadores.sort(key=lambda j: (j.get("game_score") or 0), reverse=True)
            salida.append({
                "equipo": equipo.get("displayName") or "",
                "equipo_id": str(equipo.get("id") or ""),
                "jugadores": jugadores,
            })
    return salida


def _parse_rachas(plays: list, maximo: int = 3) -> list:
    """Rachas anotadoras (8-0 o mas seguidas) sacadas del play-by-play."""
    rachas = []
    actual = None

    for p in plays or []:
        if not p.get("scoringPlay"):
            continue
        try:
            valor = int(p.get("scoreValue") or 0)
        except (TypeError, ValueError):
            valor = 0
        if valor <= 0:
            continue
        equipo = str((p.get("team") or {}).get("id") or "")
        if not equipo:
            continue

        if actual and actual["equipo"] == equipo:
            actual["puntos"] += valor
        else:
            if actual and actual["puntos"] >= 8:
                rachas.append(actual)
            actual = {
                "equipo": equipo,
                "puntos": valor,
                "periodo": (p.get("period") or {}).get("number"),
                "reloj": (p.get("clock") or {}).get("displayValue") or "",
                "local": p.get("homeScore"),
                "visita": p.get("awayScore"),
            }
    if actual and actual["puntos"] >= 8:
        rachas.append(actual)

    rachas.sort(key=lambda r: r["puntos"], reverse=True)
    return rachas[:maximo]


# ---------------------------------------------------------------------------
# PROMEDIOS DE TEMPORADA POR JUGADOR (NBA)
# ---------------------------------------------------------------------------
# ESPN los da en "byathlete": un array de valores por categoria y otro array con
# los nombres de cada posicion. Se juntan ambos y se calcula TS%.
_PROMEDIOS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete"
_PROMEDIOS_TTL = 6 * 3600  # 6 h: los promedios se mueven una vez al dia
_promedios_cache: dict = {}


def _fila_promedios(atleta: dict, categorias: list) -> dict:
    """Convierte un atleta de byathlete en un dict plano nombre->valor."""
    valores = {}
    for cat in atleta.get("categories") or []:
        nombres = next(
            (c.get("names") or [] for c in categorias if c.get("name") == cat.get("name")),
            [],
        )
        for i, valor in enumerate(cat.get("values") or []):
            if i < len(nombres):
                valores[nombres[i]] = valor

    fga = valores.get("avgFieldGoalsAttempted") or 0
    fta = valores.get("avgFreeThrowsAttempted") or 0
    pts = valores.get("avgPoints") or 0
    ts = round(pts * 100 / (2 * (fga + 0.44 * fta)), 1) if (fga or fta) else None

    def num(clave, decimales=1):
        valor = valores.get(clave)
        return round(valor, decimales) if isinstance(valor, (int, float)) else None

    return {
        "partidos": num("gamesPlayed", 0),
        "min": num("avgMinutes"),
        "pts": num("avgPoints"),
        "reb": num("avgRebounds"),
        "ast": num("avgAssists"),
        "stl": num("avgSteals"),
        "blk": num("avgBlocks"),
        "to": num("avgTurnovers"),
        "fga": num("avgFieldGoalsAttempted"),
        "fg_pct": num("fieldGoalPct"),
        "fg3_pct": num("threePointFieldGoalPct"),
        "ft_pct": num("freeThrowPct"),
        "ts": ts,
        "dd": num("doubleDouble", 0),
        "td": num("tripleDouble", 0),
    }


def _cargar_promedios_liga() -> dict:
    """Descarga los promedios de TODA la liga y los agrupa por equipo.

    ESPN ignora el filtro `team=`, pero con limit alto devuelve los ~582
    jugadores de una sola vez; se agrupan por `teamId`. Una llamada cada 6 h.
    """
    from datetime import datetime, timezone

    ahora = datetime.now(timezone.utc).timestamp()
    cache = _promedios_cache.get("liga")
    if cache and ahora - cache[0] < _PROMEDIOS_TTL:
        return cache[1]

    temporada = datetime.now(timezone.utc).year
    datos = None
    for intento in (temporada, temporada - 1):
        try:
            datos = _espn_get(
                _PROMEDIOS_URL,
                params={
                    "region": "us",
                    "lang": "en",
                    "contentorigin": "espn",
                    "isqualified": "false",
                    "page": 1,
                    "limit": 600,
                    "sort": "offensive.avgPoints:desc",
                    "season": intento,
                    "seasontype": 2,
                },
                timeout=25,
            ).json()
        except Exception as exc:
            print(f"[promedios] liga temporada {intento}: {exc!r}", flush=True)
            datos = None
        if datos and datos.get("athletes"):
            break

    if not datos:
        _promedios_cache["liga"] = (ahora, {})
        return {}

    categorias = datos.get("categories") or []
    por_equipo: dict = {}
    for fila in datos.get("athletes") or []:
        atleta = fila.get("athlete") or {}
        team_id = str(atleta.get("teamId") or "")
        if not atleta.get("id") or not team_id:
            continue
        numeros = _fila_promedios(fila, categorias)
        if not numeros.get("partidos"):
            continue
        por_equipo.setdefault(team_id, []).append({
            "id": atleta.get("id"),
            "nombre": atleta.get("displayName"),
            "corto": atleta.get("shortName") or atleta.get("displayName"),
            "foto": (atleta.get("headshot") or {}).get("href"),
            "posicion": (atleta.get("position") or {}).get("abbreviation") or "",
            "edad": atleta.get("age"),
            **numeros,
        })

    for lista in por_equipo.values():
        lista.sort(key=lambda j: (j.get("pts") or 0), reverse=True)

    _promedios_cache["liga"] = (ahora, por_equipo)
    return por_equipo


def nba_promedios_equipo(team_id, limite: int = 14) -> list:
    """Promedios de temporada de cada jugador del equipo (cache 6 h)."""
    try:
        por_equipo = _cargar_promedios_liga()
    except Exception as exc:
        print(f"[promedios] equipo {team_id}: {exc!r}", flush=True)
        return []
    return (por_equipo.get(str(team_id)) or [])[:limite]


def _parse_predictor(data: dict) -> dict | None:
    """Probabilidad de victoria del modelo de ESPN ("Matchup Predictor").

    Viene SOLO antes del partido: {"homeTeam": {"gameProjection": "59.1"}, ...}.
    Es un dato real y calibrado, mejor que una probabilidad inventada.
    """
    pred = data.get("predictor") or {}
    casa = pred.get("homeTeam") or {}
    fuera = pred.get("awayTeam") or {}
    if not casa and not fuera:
        return None
    return {
        "local": _numero(casa.get("gameProjection")),
        "visita": _numero(fuera.get("gameProjection")),
        "titulo": pred.get("header") or "",
    }


def _parse_odds_pickcenter(data: dict) -> dict | None:
    """Cuotas del partido (DraftKings via ESPN): linea, total y moneyline.

    El scoreboard de NBA suele venir sin cuotas; `pickcenter` del summary sí
    las trae y son las que usa el mercado.
    """
    centro = data.get("pickcenter") or []
    if not centro:
        return None
    elegido = centro[0] or {}
    casa = elegido.get("homeTeamOdds") or {}
    fuera = elegido.get("awayTeamOdds") or {}
    return {
        "casa": (elegido.get("provider") or {}).get("name") or "",
        "linea": elegido.get("spread"),
        "total": elegido.get("overUnder"),
        "ml_local": casa.get("moneyLine"),
        "ml_visita": fuera.get("moneyLine"),
        "detalle": elegido.get("details") or "",
    }


def _parse_injuries(data: dict) -> list:
    """Bajas por equipo con estado, zona, lado, fecha de vuelta y foto."""
    salida = []
    for bloque in data.get("injuries") or []:
        equipo = (bloque.get("team") or {}).get("displayName") or ""
        for i in bloque.get("injuries") or []:
            atleta = i.get("athlete") or {}
            nombre = atleta.get("displayName") or atleta.get("fullName") or ""
            if not nombre:
                continue
            detalles = i.get("details") or {}
            estado = i.get("status") or ""
            foto = (atleta.get("headshot") or {}).get("href")
            if not foto and atleta.get("id"):
                foto = f"https://a.espncdn.com/i/headshots/nba/players/full/{atleta['id']}.png"
            salida.append({
                "id": atleta.get("id"),
                "equipo": equipo,
                "jugador": nombre,
                "corto": atleta.get("shortName") or nombre,
                "foto": foto,
                "estado": estado,
                "grave": estado.lower() == "out",
                "zona": detalles.get("type") or "",
                "lado": detalles.get("side") or "",
                "vuelta": detalles.get("returnDate") or "",
                "comentario": (detalles.get("detail") or "")[:120],
            })
    return salida


def _parse_winprobability(data: dict, maximo: int = 120) -> list:
    """Curva de probabilidad en vivo (solo partidos en juego/finalizados).

    ESPN manda un punto por jugada (400+): se recorta a `maximo` repartiendo
    uniformemente para que la curva se dibuje igual pero pese poco.
    """
    puntos = data.get("winprobability") or []
    if not puntos:
        return []
    paso = max(1, len(puntos) // maximo)

    def _punto(p):
        local = _numero(p.get("homeWinPercentage"))
        empate = _numero(p.get("tiePercentage")) or 0.0
        visita = _numero(p.get("awayWinPercentage"))
        if visita is None and local is not None:
            visita = round(max(0.0, 1 - local - empate), 4)
        return {"local": local, "visita": visita, "empate": empate}

    recortados = [_punto(p) for p in puntos[::paso]]
    final = _punto(puntos[-1])
    if recortados and recortados[-1] != final:
        recortados.append(final)
    return recortados


def get_game_detail(sport: str, event_id: str, league_code: str | None = None) -> dict:
    """Estadisticas completas de un partido via /summary de ESPN.

    Devuelve marcador, estado, linescores, estadisticas por equipo,
    H2H, ultimos partidos y jugadores destacados.
    """
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    cache_key = f"detail:{sport}:{event_id}:{league_code or ''}"
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
            roster = competitor.get("roster") or {}
            athletes = roster.get("athletes") or ([ath] if ath else [])
            name = roster.get("displayName") or ath.get("displayName") or ath.get("fullName") or "?"
            flag = None
            for player in athletes:
                flag = player.get("headshot") or player.get("flag") or player.get("logo")
                if isinstance(flag, dict): flag = flag.get("href")
                if isinstance(flag, list): flag = flag[0] if flag else None
                if flag: break
            return {
                "id": roster.get("id") or ath.get("id"),
                "name": name,
                "short_name": roster.get("shortDisplayName") or ath.get("shortName") or name,
                "abbr": roster.get("shortDisplayName") or ath.get("shortName") or "",
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

    # Para soccer necesitamos la liga. Si el llamador la conoce (la web la pasa
    # desde el detalle) se usa tal cual; si no, se busca en el scoreboard de hoy.
    if sport == "soccer" and not league_code:
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
            "logo": _logo_equipo(team),
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
    lineups = _parse_lineups(data)

    # Datos de mercado y modelo que ESPN ya manda y antes se tiraban
    predictor = _parse_predictor(data)
    odds = _parse_odds_pickcenter(data)
    injuries = _parse_injuries(data)
    win_probability = _parse_winprobability(data) if type_info.get("state") in ("in", "post") else []

    # Analytics: cuatro factores, ritmo/eficiencia, caja de jugadores y rachas
    analytics = {}
    players_box = []
    rachas = []
    if sport == "nba" and type_info.get("state") in ("in", "post"):
        try:
            minutos = _minutos_jugados(type_info.get("state"), status.get("period"), status.get("displayClock"))
            totales = {}
            for box_team in (data.get("boxscore") or {}).get("teams") or []:
                tid = str((box_team.get("team") or {}).get("id") or "")
                if tid:
                    totales[tid] = _totales_equipo(box_team)
            if len(totales) >= 2:
                ids = list(totales)
                for i, tid in enumerate(ids):
                    rival = totales[ids[1 - i]] if len(ids) > 1 else {}
                    analytics[tid] = _analytics_equipo(totales[tid], rival, minutos)
                # Diferencia de eficiencia (el "net rating" del partido)
                if len(ids) >= 2:
                    a, b = analytics[ids[0]], analytics[ids[1]]
                    if a.get("ortg") is not None and b.get("ortg") is not None:
                        a["net"] = round(a["ortg"] - b["ortg"], 1)
                        b["net"] = round(b["ortg"] - a["ortg"], 1)
            players_box = _parse_players_box(data)
            rachas = _parse_rachas(data.get("plays"))
        except Exception as exc:  # nunca romper el detalle por un calculo extra
            print(f"[Detail] analytics nba {event_id}: {exc!r}", flush=True)
            analytics, players_box, rachas = {}, [], []

    # Asignar ultimos partidos a cada equipo
    for t in teams_out:
        t["recent_games"] = recent.get(str(t["id"]), {}).get("recent", [])
        t["analytics"] = analytics.get(str(t["id"]))

    # Promedios de temporada de cada jugador (solo NBA): alimenta la pestaña
    # Alineacion, que hasta ahora salia VACIA en baloncesto.
    if sport == "nba":
        for t in teams_out:
            try:
                t["promedios"] = nba_promedios_equipo(t.get("id"))
            except Exception as exc:
                print(f"[Detail] promedios team={t.get('id')}: {exc!r}", flush=True)
                t["promedios"] = []

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
        "league_code": league_code,
        "state": type_info.get("state"),
        "status": type_info.get("shortDetail", ""),
        "clock": status.get("displayClock", ""),
        "period": status.get("period"),
        "situation": situation,
        "date": comp.get("date") or "",
        "teams": teams_out,
        "head_to_head": h2h,
        "key_players": key_players,
        "lineups": lineups,
        "predictor": predictor,
        "odds": odds,
        "injuries": injuries,
        "win_probability": win_probability,
        "players_box": players_box,
        "runs": rachas,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(cache_key, result)
    return result


_inflight: dict = {}
_inflight_guard = threading.Lock()


def _inflight_lock_for(key: str) -> threading.Lock:
    """Lock por clave: evita que dos peticiones simultaneas (usuario +
    calentador de cache) le peguen a ESPN a la vez con el cache vencido."""
    with _inflight_guard:
        if key not in _inflight:
            _inflight[key] = threading.Lock()
        return _inflight[key]


def get_sport_games(sport: str, league: str | None = None) -> dict:
    """Partidos de un deporte: en vivo, proximos y finalizados (cache 60s).

    Si dos peticiones llegan juntas con el cache vencido, solo una consulta
    a ESPN y la otra reutiliza el resultado.
    """
    sport = (sport or "").strip().lower()
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    path, label = SPORTS[sport]
    cache_key = f"sport_games:{sport}:{league or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    with _inflight_lock_for(cache_key):
        # Doble chequeo: otro hilo pudo llenar el cache mientras esperabamos.
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        return _sport_games_fresh(sport, league, path, label, cache_key)


def _sport_games_fresh(sport: str, league: str | None, path: str, label: str, cache_key: str) -> dict:
    """Version sin cache: consulta ESPN (llamar SIEMPRE con el lock tomado)."""
    games: list = []
    error = None

    if sport == "tennis":
        # El tenis usa su propio parser (eventos anidados por torneo)
        try:
            data = _fetch_scoreboard(path)
            games = _parse_tennis(data.get("events", []))
        except Exception as exc:
            error = str(exc)
    elif sport == "soccer":
        # Futbol: todas las ligas registradas (o una sola si filtran)
        ligas = {league: SOCCER_LEAGUES[league]} if league in SOCCER_LEAGUES else SOCCER_LEAGUES
        from concurrent.futures import ThreadPoolExecutor

        def _liga(args):
            codigo, nombre = args
            try:
                data = _fetch_scoreboard(path, codigo)
            except Exception as exc:
                return [], str(exc)
            out = [
                _parse_event(ev, nombre, codigo)
                for ev in data.get("events", [])
            ]
            return out, None

        # 1a pasada: todas las ligas en paralelo. Si ESPN bloquea alguna por
        # rafaga (403/429), esa liga falla completa pero no tira las demas.
        fallidas: list = []
        errores: list = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for args, (resultado, err) in zip(list(ligas.items()), ex.map(_liga, ligas.items())):
                if err:
                    fallidas.append(args)
                    errores.append(err)
                    continue
                games.extend(resultado)

        # 2a pasada: reintentar en SERIE las fallidas. ESPN bloquea rafagas
        # paralelas; liga por liga (con respiro entre cada una) casi siempre
        # recupera. Con tope de ~20s para no demorar la respuesta si ESPN
        # esta caida de verdad.
        if fallidas:
            import time as _time

            limite = _time.monotonic() + 20
            for args in fallidas:
                if _time.monotonic() > limite:
                    break
                _time.sleep(0.4)
                resultado, err = _liga(args)
                if not err:
                    games.extend(resultado)

        # Con que UNA liga cargue ya hay partidos: NO se reporta error global
        # (el banner rojo del frontend taparia los 50+ partidos que si
        # cargaron). El error solo sale si no se recupero NINGUNA liga.
        if games:
            error = None
        elif errores:
            error = errores[0]
    else:
        # NBA, MLB, NFL, MMA: un solo scoreboard por deporte
        try:
            data = _fetch_scoreboard(path, league)
            games = [
                _parse_event(ev, label, league)
                for ev in data.get("events", [])
            ]
        except Exception as exc:
            error = str(exc)

    # Etiquetar deporte y completar campos que faltan por juego
    for g in games:
        g["sport"] = sport
        if not g.get("sport_path"):
            g["sport_path"] = path
        g.setdefault("home_linescores", [])
        g.setdefault("away_linescores", [])
        if g.get("home") is None:
            g["home"] = {}
        if g.get("away") is None:
            g["away"] = {}

    # Orden: en vivo primero, luego por hora
    games.sort(key=lambda g: (g.get("state") != "in", g.get("date") or ""))

    live_count = sum(1 for g in games if g.get("state") == "in")
    result = {
        "sport": sport,
        "label": label,
        "games": games,
        "live_count": live_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        result["error"] = error
    if error and not games:
        # No cachear fallos: el proximo auto-refresh (60s) reintenta contra
        # ESPN en vez de servir el mismo error desde cache.
        return result
    _cache_set(cache_key, result)
    return result


def filtrar_por_dia(data: dict, delta: int) -> dict:
    """Filtra los juegos de una respuesta get_sport_games por dia (hora Nicaragua).

    delta: -1 ayer, 0 hoy, 1 manana, 2 pasado manana. Los partidos EN VIVO
    siempre pasan el filtro (un partido en curso es de hoy por definicion).
    """
    hoy = datetime.now(timezone.utc).astimezone(TZ_NIC).date()

    def _delta_de(g: dict):
        fecha = (g.get("date") or "").strip()
        if not fecha:
            return None
        try:
            local = datetime.fromisoformat(fecha.replace("Z", "+00:00")).astimezone(TZ_NIC)
            return (local.date() - hoy).days
        except (ValueError, TypeError):
            return None

    filtrados = [
        g for g in (data.get("games") or [])
        if g.get("state") == "in" or _delta_de(g) == delta
    ]
    out = dict(data)
    out["games"] = filtrados
    out["live_count"] = sum(1 for g in filtrados if g.get("state") == "in")
    out["day"] = delta
    return out


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