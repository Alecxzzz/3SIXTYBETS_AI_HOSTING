"""
Servicio unificador de estadísticas de jugadores (MLB + Fútbol).
Expone la misma interfaz para ambos deportes y cachea en memoria
para no repetir llamadas (crítico por el límite de API-Sports).
"""

import time
import threading

from .mlb_api import (
    get_boxscore as mlb_boxscore,
    get_player_last5 as mlb_last5,
)
from .football_api import (
    get_fixture_lineup as football_lineup,
    get_player_last5 as football_last5,
)

# Caché en memoria con TTL: {clave: (timestamp, valor)}
_CACHE: dict = {}
_CACHE_TTL = 60 * 10  # 10 minutos
_LOCK = threading.Lock()

# Deportes soportados y su grupo de stats por defecto en MLB
_MLB_GROUPS = ("pitching", "hitting")


def _cache_get(key: str):
    with _LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > _CACHE_TTL:
            del _CACHE[key]
            return None
        return val


def _cache_set(key: str, value):
    with _LOCK:
        _CACHE[key] = (time.time(), value)


def cache_clear():
    """Limpia todo el caché (útil para tests o admin)."""
    with _LOCK:
        _CACHE.clear()


def cache_stats() -> dict:
    """Info del caché para el panel admin."""
    with _LOCK:
        return {"entradas": len(_CACHE), "ttl_segundos": _CACHE_TTL}


def _try_mlb(player_id: int, season: int) -> dict:
    """
    MLB: el gameLog puede ser 'pitching' o 'hitting'.
    Probamos el grupo que tenga datos.
    """
    for group in _MLB_GROUPS:
        games = mlb_last5(player_id, season, group)
        if games:
            return {
                "player_name": None,  # se resuelve en el servicio
                "sport": "mlb",
                "group": group,
                "last_5_games": games,
            }
    return {"player_name": None, "sport": "mlb", "group": None, "last_5_games": []}


def _try_football(player_id: int, season: int, team_id=None) -> dict:
    games = football_last5(player_id, season, team_id)
    return {
        "player_name": None,
        "sport": "football",
        "group": None,
        "last_5_games": games,
    }


def get_last5(sport: str, player_id: int, season: int = 2024) -> dict:
    """
    Punto de entrada unificado.
    Devuelve:
      { player_name, sport, last_5_games: [ {date, opponent, stats}, ... ] }
    Nunca lanza excepciones: siempre devuelve JSON manejable.
    """
    sport = (sport or "").lower()
    # Aceptamos sinónimos del frontend
    if sport in ("soccer", "futbol", "football"):
        sport = "football"

    if sport not in ("mlb", "football"):
        return {"error": True, "message": f"Deporte no soportado: {sport}"}

    cache_key = f"last5:{sport}:{player_id}:{season}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        if sport == "mlb":
            result = _try_mlb(player_id, season)
        else:
            result = _try_football(player_id, season)
    except Exception as e:
        print(f"[PlayerStats] Error inesperado ({sport}/{player_id}): {e}")
        return {"error": True, "message": "No se pudieron cargar las estadísticas"}

    # Resolver nombre del jugador (una llamada extra solo si falta)
    result["player_name"] = result.get("player_name") or _resolve_name(
        sport, player_id, season
    )

    _cache_set(cache_key, result)
    return result


def _resolve_name(sport: str, player_id: int, season: int):
    """Obtiene el nombre real del jugador desde su fuente."""
    try:
        if sport == "mlb":
            import requests as _rq

            r = _rq.get(
                f"https://statsapi.mlb.com/api/v1/people/{player_id}",
                timeout=8,
            )
            r.raise_for_status()
            people = r.json().get("people", [])
            return people[0].get("fullName") if people else None
        else:
            import os
            import requests as _rq

            r = _rq.get(
                "https://v3.football.api-sports.io/players",
                params={"id": player_id, "season": season},
                headers={"x-apisports-key": os.environ.get("FOOTBALL_API_KEY", "")},
                timeout=8,
            )
            r.raise_for_status()
            resp = r.json().get("response", [])
            if resp:
                return resp[0].get("player", {}).get("name")
        return None
    except Exception as e:
        print(f"[PlayerStats] Error resolviendo nombre ({sport}/{player_id}): {e}")
        return None


def get_clickable_players(sport: str, game_id: int, season: int = 2024) -> dict:
    """
    Lista de jugadores clickeables de un partido:
      - MLB: boxscore (game_pk)
      - Fútbol: lineup (fixture_id)
    """
    sport = (sport or "").lower()
    if sport in ("soccer", "futbol"):
        sport = "football"
    cache_key = f"players:{sport}:{game_id}:{season}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        if sport == "mlb":
            players = mlb_boxscore(game_id).get("players", [])
        elif sport == "football":
            players = football_lineup(game_id)
        else:
            return {"error": True, "message": f"Deporte no soportado: {sport}"}
    except Exception as e:
        print(f"[PlayerStats] Error en get_clickable_players ({sport}/{game_id}): {e}")
        return {"error": True, "message": "No se pudo obtener la lista de jugadores"}

    result = {"sport": sport, "players": players}
    _cache_set(cache_key, result)
    return result


class _PlayerStatsService:
    """Wrapper con la interfaz pública del servicio."""
    get_last5 = staticmethod(get_last5)
    get_clickable_players = staticmethod(get_clickable_players)
    cache_stats = staticmethod(cache_stats)
    cache_clear = staticmethod(cache_clear)


player_stats_service = _PlayerStatsService()
