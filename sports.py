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

# Ligas de futbol que seguimos (codigo ESPN -> nombre legible)
SOCCER_LEAGUES = {
    "eng.1": "Premier League",
    "esp.1": "LaLiga",
    "ita.1": "Serie A",
    "ger.1": "Bundesliga",
    "fra.1": "Ligue 1",
    "uefa.champions": "Champions League",
    "mex.1": "Liga MX",
    "usa.1": "MLS",
}

# Deportes soportados: clave -> (path ESPN, etiqueta)
SPORTS = {
    "soccer": ("soccer", "Futbol"),
    "nba": ("basketball/nba", "NBA"),
    "mlb": ("baseball/mlb", "MLB"),
    "nfl": ("football/nfl", "NFL"),
    "tennis": ("tennis/atp", "Tenis"),
}

CACHE_TTL_SECONDS = 60
DETAIL_CACHE_TTL_SECONDS = 30
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


def _fetch_scoreboard(path: str, league: str | None = None) -> dict:
    # En soccer la liga va en el path (soccer/eng.1/scoreboard), no como query param
    if league and path.startswith("soccer"):
        url = f"{ESPN_BASE}/{path}/{league}/scoreboard"
    else:
        url = f"{ESPN_BASE}/{path}/scoreboard"
    resp = http_requests.get(url, timeout=10)
    resp.raise_for_status()
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

    return {
        "id": team.get("id"),
        "name": team.get("displayName", team.get("name", "?")),
        "abbr": team.get("abbreviation", ""),
        "logo": logo,
        "score": _parse_number(raw_score),
        "winner": team.get("winner"),
    }


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
        parsed = _parse_team(competitor.get("team", {}))
        # Fallback: a veces el score viene en el competitor y no en team
        if parsed["score"] is None:
            parsed["score"] = _parse_number(competitor.get("score"))
        recs = [r for r in (competitor.get("records") or []) if r]
        parsed["record"] = recs[0].get("summary") if recs else None
        lines = _linescores(competitor)
        if competitor.get("homeAway") == "home":
            home = parsed
            home_linescores = lines
        else:
            away = parsed
            away_linescores = lines

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
    """El tenis de ESPN tiene estructura distinta: competitions anidadas por grupo."""
    out = []
    for event in events:
        for comp in event.get("competitions", []):
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            home = _parse_team(competitors[0].get("team", {}))
            away = _parse_team(competitors[1].get("team", {}))
            status = event.get("status", {})
            type_info = status.get("type", {})
            out.append({
                "id": event.get("id"),
                "sport_path": None,
                "league_code": None,
                "league": "Tenis",
                "date": event.get("date"),
                "state": type_info.get("state"),
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


def get_sport_games(sport: str) -> dict:
    """Devuelve los partidos (en vivo, proximos y finalizados) de un deporte."""
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    cached = _cache_get(f"sport:{sport}")
    if cached is not None:
        return cached

    path, label = SPORTS[sport]
    games = []

    try:
        if sport == "soccer":
            for league_code, league_name in SOCCER_LEAGUES.items():
                try:
                    data = _fetch_scoreboard(path, league=league_code)
                    for event in data.get("events", []):
                        games.append(_parse_event(event, league_name, league_code))
                except Exception:
                    continue  # una liga que falla no tira todo el deporte
        elif sport == "tennis":
            data = _fetch_scoreboard(path)
            games = _parse_tennis(data.get("events", []))
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
        "live_count": sum(1 for g in games if g["state"] == "in"),
        "games": games,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(f"sport:{sport}", result)
    return result


# ==============================
# DETALLE DE UN PARTIDO
# ==============================

def get_game_detail(sport: str, event_id: str) -> dict:
    """Estadisticas completas de un partido via /summary de ESPN.

    Devuelve marcador, estado, linescores y estadisticas comparadas por equipo.
    """
    if sport not in SPORTS:
        raise ValueError(f"Deporte no soportado: {sport}")

    cache_key = f"detail:{sport}:{event_id}"
    cached = _cache_get(cache_key, ttl=DETAIL_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    path, label = SPORTS[sport]

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
    resp = http_requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

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
            "abbr": team.get("abbreviation", ""),
            "logo": (team.get("logo") or [None])[0] if isinstance(team.get("logo"), list) else team.get("logo"),
            "score": _parse_number(competitor.get("score")),
            "winner": competitor.get("winner"),
            "homeAway": competitor.get("homeAway"),
            "linescores": _linescores(competitor),
            "records": [r.get("summary") for r in (competitor.get("records") or []) if r],
        })

    # Estadisticas comparadas desde boxscore.teams[].statistics[]
    # Dos formatos segun deporte:
    #  - Plano (NBA/NFL): {name, displayValue}
    #  - Por categoria (MLB): {name: "batting", stats: [{name, displayName, displayValue}]}
    def _flatten_stats(items):
        out = []
        for s in items or []:
            if not s:
                continue
            if "stats" in s:
                category = s.get("displayName") or s.get("name") or ""
                for sub in s["stats"] or []:
                    if sub:
                        label = sub.get("displayName") or sub.get("shortDisplayName") or sub.get("name") or ""
                        prefix = f"{category} - {label}" if category else label
                        out.append({
                            "name": prefix,
                            "label": sub.get("displayValue", ""),
                        })
            else:
                out.append({
                    "name": s.get("displayName") or s.get("name") or s.get("abbreviation") or "",
                    "label": s.get("displayValue", ""),
                })
        return out

    stats_by_team = {}
    for box_team in data.get("boxscore", {}).get("teams", []):
        team_id = str((box_team.get("team") or {}).get("id"))
        stats_by_team[team_id] = _flatten_stats(box_team.get("statistics"))

    for t in teams_out:
        t["statistics"] = stats_by_team.get(str(t["id"]), [])

    result = {
        "sport": sport,
        "label": label,
        "event_id": event_id,
        "state": type_info.get("state"),
        "status": type_info.get("shortDetail", ""),
        "clock": status.get("displayClock", ""),
        "period": status.get("period"),
        "teams": teams_out,
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