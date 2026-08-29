"""
Fuente MLB — statsapi.mlb.com (oficial, gratis, sin API key).
Todas las funciones devuelven datos reales y manejan errores sin crashear.
"""

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 10

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 3SIXTYBETS/1.0)"}


def get_team_id(team_name: str):
    """Devuelve el id del equipo de MLB por nombre. None si no existe."""
    try:
        r = requests.get(
            f"{BASE_URL}/teams",
            params={"sportId": 1},
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        for team in r.json().get("teams", []):
            name = (team.get("name") or "").lower()
            frac = (team.get("franchiseName") or "").lower()
            club = (team.get("clubName") or "").lower()
            q = team_name.lower().strip()
            if q in name or q in frac or q in club:
                return team["id"]
        return None
    except Exception as e:
        print(f"[MLB] Error en get_team_id('{team_name}'): {e}")
        return None


def get_team_schedule(team_id: int, season: int):
    """Calendario de partidos de un equipo por temporada."""
    try:
        r = requests.get(
            f"{BASE_URL}/schedule",
            params={"sportId": 1, "teamId": team_id, "season": season},
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[MLB] Error en get_team_schedule(team={team_id}): {e}")
        return {}


def get_h2h(team_id: int, opponent_id: int, season: int):
    """Historial cara a cara entre dos equipos en una temporada."""
    try:
        r = requests.get(
            f"{BASE_URL}/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "opponentId": opponent_id,
                "season": season,
            },
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[MLB] Error en get_h2h({team_id} vs {opponent_id}): {e}")
        return {}


def get_boxscore(game_pk: int):
    """Boxscore de un partido: jugadores de ambos equipos (clickeables)."""
    try:
        r = requests.get(
            f"{BASE_URL}/game/{game_pk}/boxscore",
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        players = {}
        for side in ("away", "home"):
            team = data.get("teams", {}).get(side, {})
            team_name = team.get("team", {}).get("name", side.upper())
            for pid, info in (team.get("players") or {}).items():
                person = info.get("person", {})
                players[str(person.get("id"))] = {
                    "id": person.get("id"),
                    "name": person.get("fullName", "?"),
                    "team": team_name,
                    "position": (info.get("position") or {}).get("abbreviation", ""),
                    "side": side,
                }
        return {"game_pk": game_pk, "players": list(players.values())}
    except Exception as e:
        print(f"[MLB] Error en get_boxscore(game={game_pk}): {e}")
        return {"game_pk": game_pk, "players": [], "error": str(e)}


def _fmt_pitcher(row: dict) -> dict:
    """Formatea una fila del gameLog para un pitcher."""
    stat = row.get("stat", {})
    opp = row.get("opponent") or row.get("opposingTeam") or {}
    return {
        "date": row.get("date", ""),
        "opponent": opp.get("name", "?"),
        "stats": {
            "IP": stat.get("inningsPitched", "0.0"),
            "H": stat.get("hits", 0),
            "R": stat.get("runs", 0),
            "ER": stat.get("earnedRuns", 0),
            "BB": stat.get("baseOnBalls", 0),
            "K": stat.get("strikeOuts", 0),
            "ERA": stat.get("era", "-"),
            "WHIP": stat.get("whip", "-"),
        },
    }


def _fmt_batter(row: dict) -> dict:
    """Formatea una fila del gameLog para un bateador."""
    stat = row.get("stat", {})
    opp = row.get("opponent") or row.get("opposingTeam") or {}
    return {
        "date": row.get("date", ""),
        "opponent": opp.get("name", "?"),
        "stats": {
            "AB": stat.get("atBats", 0),
            "H": stat.get("hits", 0),
            "HR": stat.get("homeRuns", 0),
            "RBI": stat.get("rbi", 0),
            "BB": stat.get("baseOnBalls", 0),
            "K": stat.get("strikeOuts", 0),
            "AVG": stat.get("avg", "-"),
        },
    }


def get_player_last5(player_id: int, season: int, group: str):
    """
    Últimos 5 juegos reales de un jugador de MLB.
    group = "pitching" | "hitting"
    """
    try:
        r = requests.get(
            f"{BASE_URL}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": group, "season": season},
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        splits = []
        for split in data.get("stats", []):
            splits.extend(split.get("splits", []))
        # Ordenar por fecha descendente y tomar los primeros 5
        splits.sort(key=lambda s: s.get("date", ""), reverse=True)
        formatter = _fmt_pitcher if group == "pitching" else _fmt_batter
        return [formatter(row) for row in splits[:5]]
    except Exception as e:
        print(f"[MLB] Error en get_player_last5(player={player_id}): {e}")
        return []
