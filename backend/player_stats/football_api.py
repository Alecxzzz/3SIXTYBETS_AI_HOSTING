"""
Fuente Fútbol — API-Sports (v3.football.api-sports.io).
Requiere FOOTBALL_API_KEY en el .env.
OJO: el free tier tiene límite de ~100 req/día, por eso el caché es clave.
"""

import os
import requests

BASE_URL = "https://v3.football.api-sports.io"
TIMEOUT = 10


def _headers():
    key = os.environ.get("FOOTBALL_API_KEY", "")
    if not key:
        raise RuntimeError("Falta la variable de entorno FOOTBALL_API_KEY")
    return {"x-apisports-key": key}


def get_team_id(team_name: str):
    """Devuelve el id del equipo de fútbol por nombre. None si no existe."""
    try:
        r = requests.get(
            f"{BASE_URL}/teams",
            params={"name": team_name},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("response", [])
        if results:
            return results[0]["team"]["id"]
        return None
    except Exception as e:
        print(f"[Football] Error en get_team_id('{team_name}'): {e}")
        return None


def get_team_fixtures(team_id: int, season: int, last: int = 10):
    """Últimos N partidos de un equipo en una temporada."""
    try:
        r = requests.get(
            f"{BASE_URL}/fixtures",
            params={"team": team_id, "season": season, "last": last},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("response", [])
    except Exception as e:
        print(f"[Football] Error en get_team_fixtures(team={team_id}): {e}")
        return []


def get_h2h(team_id_1: int, team_id_2: int):
    """Historial cara a cara entre dos equipos."""
    try:
        r = requests.get(
            f"{BASE_URL}/fixtures/headtohead",
            params={"h2h": f"{team_id_1}-{team_id_2}"},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("response", [])
    except Exception as e:
        print(f"[Football] Error en get_h2h({team_id_1}-{team_id_2}): {e}")
        return []


def get_fixture_lineup(fixture_id: int):
    """Alineaciones (titulares/suplentes) de ambos equipos en un partido."""
    try:
        r = requests.get(
            f"{BASE_URL}/fixtures/lineups",
            params={"fixture": fixture_id},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        players = []
        for team in r.json().get("response", []):
            team_name = team.get("team", {}).get("name", "?")
            for group in ("startXI", "substitutes"):
                for p in team.get(group, []):
                    player = p.get("player", {})
                    players.append({
                        "id": player.get("id"),
                        "name": player.get("name", "?"),
                        "team": team_name,
                        "position": (player.get("pos") or ""),
                        "starter": group == "startXI",
                    })
        return players
    except Exception as e:
        print(f"[Football] Error en get_fixture_lineup(fixture={fixture_id}): {e}")
        return []


# Mapa auxiliar player_id -> team_id para detectar el rival
_team_of_player: dict = {}


def _fmt_row(player_row: dict, fixture: dict) -> dict:
    """Formatea una fila de /fixtures/players para un jugador."""
    stats = (player_row.get("statistics") or [{}])[0]
    games = stats.get("games", {}) or {}
    goals = stats.get("goals", {}) or {}
    date = (fixture.get("fixture") or {}).get("date", "")
    teams = fixture.get("teams", {}) or {}
    pid = player_row.get("player", {}).get("id")
    my_team = _team_of_player.get(pid)
    rival = "?"
    for side in ("home", "away"):
        t = teams.get(side, {}) or {}
        if t.get("id") != my_team:
            rival = t.get("name", "?")
            break
    return {
        "date": (date or "")[:10],
        "opponent": rival,
        "stats": {
            "min": games.get("minutes", 0),
            "rating": games.get("rating", "-"),
            "goles": goals.get("total", 0) or 0,
            "asist": goals.get("assists", 0) or 0,
            "amarillas": (stats.get("cards", {}) or {}).get("yellow", 0) or 0,
            "rojas": (stats.get("cards", {}) or {}).get("red", 0) or 0,
        },
    }


def get_player_last5(player_id: int, season: int, team_id: int = None):
    """
    Últimos 5 juegos REALES de un jugador de fútbol.
    API-Sports no da gameLog directo, entonces:
      a) fixtures del equipo (últimos 15)
      b) por cada fixture, /fixtures/players
      c) filtrar filas del player_id
      d) primeros 5 con datos, ordenados por fecha descendente
    """
    try:
        if team_id is None:
            r = requests.get(
                f"{BASE_URL}/players",
                params={"id": player_id, "season": season},
                headers=_headers(),
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            resp = r.json().get("response", [])
            if not resp:
                return []
            team_id = ((resp[0].get("statistics") or [{}])[0].get("team") or {}).get("id")
            if not team_id:
                return []

        _team_of_player[player_id] = team_id
        fixtures = get_team_fixtures(team_id, season, last=15)
        games = []
        for fx in fixtures:
            fx_id = (fx.get("fixture") or {}).get("id")
            if not fx_id:
                continue
            try:
                r = requests.get(
                    f"{BASE_URL}/fixtures/players",
                    params={"fixture": fx_id},
                    headers=_headers(),
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                for team_block in r.json().get("response", []):
                    for prow in team_block.get("players", []):
                        if prow.get("player", {}).get("id") == player_id:
                            games.append(_fmt_row(prow, fx))
                            break
            except Exception as e:
                print(f"[Football] fixtures/players fx={fx_id}: {e}")
                continue
            if len(games) >= 5:
                break
        games.sort(key=lambda g: g["date"], reverse=True)
        return games[:5]
    except Exception as e:
        print(f"[Football] Error en get_player_last5(player={player_id}): {e}")
        return []
