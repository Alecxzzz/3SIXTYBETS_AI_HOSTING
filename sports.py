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
_cache = {}  # clave -> {"data": ..., "ts": epoch}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL_SECONDS:
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


def _parse_team(team: dict) -> dict:
    """Normaliza un equipo de la respuesta de ESPN."""
    score = None
    for item in team.get("score", []) if isinstance(team.get("score"), list) else []:
        pass
    # ESPN a veces manda score como string, a veces como lista
    raw_score = team.get("score")
    if isinstance(raw_score, list):
        raw_score = raw_score[0].get("value") if raw_score else None
    if raw_score not in (None, ""):
        try:
            score = int(float(raw_score))
        except (TypeError, ValueError):
            score = raw_score

    return {
        "id": team.get("id"),
        "name": team.get("displayName", team.get("name", "?")),
        "abbr": team.get("abbreviation", ""),
        "logo": (team.get("logo") or [None])[0] if isinstance(team.get("logo"), list) else team.get("logo"),
        "score": score,
        "winner": team.get("winner"),
    }


def _parse_event(event: dict, league_label: str) -> dict:
    comp = (event.get("competitions") or [{}])[0]

    home, away = {}, {}
    for competitor in comp.get("competitors", []):
        parsed = _parse_team(competitor.get("team", {}))
        parsed["record"] = (competitor.get("records") or [{}])[0].get("summary")
        if competitor.get("homeAway") == "home":
            home = parsed
        else:
            away = parsed

    status = event.get("status", {})
    type_info = status.get("type", {})
    state = type_info.get("state")  # pre | in | post

    # Detalle del reloj / periodo
    display_clock = status.get("displayClock") or type_info.get("shortDetail") or ""
    period = status.get("period")

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
        "league": league_label,
        "date": event.get("date"),
        "state": state,  # pre = proximo, in = en vivo, post = finalizado
        "status": type_info.get("shortDetail", ""),
        "clock": display_clock,
        "period": period,
        "home": home,
        "away": away,
        "odds": odds,
        "summary": event.get("links", [{}])[0].get("href"),
    }


def _parse_tennis(events: list) -> list:
    """El tenis de ESPN tiene estructura distinta: competitions anidadas por grupo."""
    out = []
    for event in events:
        league_label = "Tenis"
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
                "league": league_label,
                "date": event.get("date"),
                "state": type_info.get("state"),
                "status": type_info.get("shortDetail", ""),
                "clock": status.get("displayClock", ""),
                "period": None,
                "home": home,
                "away": away,
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
                        games.append(_parse_event(event, league_name))
                except Exception:
                    continue  # una liga que falla no tira todo el deporte
        elif sport == "tennis":
            data = _fetch_scoreboard(path)
            games = _parse_tennis(data.get("events", []))
        else:
            data = _fetch_scoreboard(path)
            for event in data.get("events", []):
                games.append(_parse_event(event, label))
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