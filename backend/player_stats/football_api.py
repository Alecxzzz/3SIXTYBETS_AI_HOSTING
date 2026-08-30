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


# =====================================================================
# FALLBACK ESPN (gratis, sin API key) - usa datos reales de ESPN
# =====================================================================
_ESPN_BASES = [
    "https://site.api.espn.com/apis/site/v2/sports/soccer",
    "https://site.web.api.espn.com/apis/site/v2/sports/soccer",
]
_ESPN_BASE = _ESPN_BASES[0]
_ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.espn.com/",
}


def _espn_get_json(path: str, timeout: int = 12):
    """GET a ESPN probando el host principal y el mirror si hay 403/429."""
    import time as _t
    last_exc = None
    for base in _ESPN_BASES:
        for intento in range(2):
            try:
                r = requests.get(f"{base}/{path}", timeout=timeout, headers=_ESPN_HEADERS)
                if r.status_code in (403, 429) and intento < 1:
                    _t.sleep(1.5)
                    continue
                if r.status_code in (403, 429):
                    break
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_exc = e
                if intento < 1:
                    _t.sleep(1.5)
    raise last_exc
_summary_cache: dict = {}  # event_id o sched:tid -> (timestamp, data)


def _espn_team_recent_events(team_id, league, limit=6):
    """Últimos eventos finalizados de un equipo vía ESPN (caché 15 min)."""
    import time as _t
    cache_key = f"sched:{team_id}:{league}"
    cached = _summary_cache.get(cache_key)
    if cached and _t.time() - cached[0] < 900:
        return cached[1]
    data = _espn_get_json(f"{league}/teams/{team_id}/schedule")
    events = [
        ev for ev in (data or {}).get("events", [])
        if (ev.get("status") or {}).get("type", {}).get("state", "") == "post"
    ]
    events = events[-limit:]
    _summary_cache[cache_key] = (_t.time(), events)
    return events


# Traducción de claves de stats de ESPN al español
_ESPN_STAT_ES = {
    "mins": "Min", "minutes": "Min", "goals": "Goles", "goal": "Goles",
    "assists": "Asist", "assist": "Asist", "shots": "Tiros", "shot": "Tiros",
    "shotsOnTarget": "Tiros a puerta", "shots_on_target": "Tiros a puerta",
    "onTargetShots": "Tiros a puerta", "yellowCards": "Amarillas", "yellow": "Amarillas",
    "redCards": "Rojas", "red": "Rojas", "passes": "Pases", "passAccuracy": "% Pases",
    "tackles": "Entradas", "interceptions": "Intercepciones", "saves": "Atajadas",
    "cleanSheet": "Valla invicta", "foulsCommitted": "Faltas", "offsides": "Fueras de juego",
    "duelsWon": "Duelos ganados", "aerialsWon": "Aéreos ganados", "clearances": "Despejes",
    "accuratePasses": "Pases completos", "totalPasses": "Pases", "keyPasses": "Pases clave",
    "bigChancesCreated": "Ocasiones claras creadas", "bigChancesMissed": "Ocasiones claras fallidas",
    "hitWoodwork": "Al palo", "ownGoals": "Goles en propia", "rating": "Rating",
    "dribblesCompleted": "Regates", "dribblesAttempted": "Regates intentados",
}


def _espn_parse_athlete_stats(summary: dict, player_id: str):
    """Busca un jugador en el boxscore de un summary de ESPN y devuelve sus stats."""
    box = summary.get("boxscore") or {}
    for team_block in box.get("players", []) or []:
        for category in team_block.get("statistics", []) or []:
            keys = [k.get("name", "") if isinstance(k, dict) else str(k) for k in category.get("keys", [])]
            labels = [k.get("displayName", "") for k in category.get("keys", []) if isinstance(k, dict)] or keys
            for ath in category.get("athletes", []) or []:
                a = ath.get("athlete") or {}
                if str(a.get("id")) == str(player_id):
                    raw = ath.get("stats") or []
                    stats = {}
                    for i, val in enumerate(raw):
                        if i >= len(keys):
                            break
                        name = _ESPN_STAT_ES.get(keys[i], labels[i] if i < len(labels) else keys[i])
                        if val not in (None, "-", "--"):
                            stats[name] = val
                    return stats
    return None

def get_player_last5_espn(player_id, league, team_ids=None, season=None) -> list:
    """
    Últimas 5 actuaciones de un futbolista usando ESPN (gratis, datos reales).
    Devuelve [{date, opponent, stats}] ordenado por fecha desc (máx 5).
    """
    import time as _t
    if not league or not team_ids:
        return []
    seen = set()
    games = []
    for tid in team_ids:
        try:
            events = _espn_team_recent_events(tid, league)
        except Exception as e:
            print(f"[Football-ESPN] Error schedule team={tid}: {e}")
            continue
        for ev in events:
            eid = str(ev.get("id"))
            if eid in seen:
                continue
            seen.add(eid)
            try:
                cached = _summary_cache.get(f"sum:{eid}")
                if cached and _t.time() - cached[0] < 900:
                    summary = cached[1]
                else:
                    summary = _espn_get_json(f"{league}/summary?event={eid}")
                    if not summary:
                        continue
                    _summary_cache[f"sum:{eid}"] = (_t.time(), summary)
                comps = ((summary.get("header") or {}).get("competitions") or [{}])[0]
                opponent = "?"
                for comp in comps.get("competitors", []) or []:
                    tinfo = comp.get("team") or {}
                    name = tinfo.get("displayName") or tinfo.get("shortDisplayName")
                    if name and str(tinfo.get("id")) != str(tid):
                        opponent = name
                        break
                stats = _espn_parse_athlete_stats(summary, player_id)
                if stats:
                    games.append({"date": ev.get("date", ""), "opponent": opponent, "stats": stats})
            except Exception as e:
                print(f"[Football-ESPN] Error summary event={eid}: {e}")
                continue
            if len(games) >= 8:
                break
        if len(games) >= 8:
            break
    games.sort(key=lambda g: g["date"], reverse=True)
    return games[:5]

def get_player_last5_from_events(player_id, league, events, own_team_id=None) -> list:
    """
    Variante del fallback ESPN que recibe eventos ya obtenidos (ej. del
    scoreboard por fechas de sports.py). Devuelve [{date, opponent, stats}].
    """
    import time as _t
    if not league or not events:
        return []
    games = []
    for ev in events:
        eid = str(ev.get("id"))
        try:
            cached = _summary_cache.get(f"sum:{eid}")
            if cached and _t.time() - cached[0] < 900:
                summary = cached[1]
            else:
                summary = _espn_get_json(f"{league}/summary?event={eid}")
                if not summary:
                    continue
                _summary_cache[f"sum:{eid}"] = (_t.time(), summary)
            comps = ((summary.get("header") or {}).get("competitions") or [{}])[0]
            opponent = "?"
            for comp in comps.get("competitors", []) or []:
                tinfo = comp.get("team") or {}
                name = tinfo.get("displayName") or tinfo.get("shortDisplayName")
                if name and own_team_id and str(tinfo.get("id")) != str(own_team_id):
                    opponent = name
                    break
            stats = _espn_parse_athlete_stats(summary, player_id)
            if stats:
                games.append({"date": ev.get("date", ""), "opponent": opponent, "stats": stats})
        except Exception as e:
            print(f"[Football-ESPN] Error summary event={eid}: {e}")
            continue
    return games

