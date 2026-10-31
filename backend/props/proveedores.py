# -*- coding: utf-8 -*-
"""
Historico POR PARTIDO de un jugador (ultimos N).

Fuentes (gratuitas, sin API key; verificadas el 30/10/2026):

    soccer / nba / nfl -> ESPN
        /apis/common/v3/sports/{path}/athletes/{id}/gamelog
        soccer  (Gerard Moreno): totalGoals, goalAssists, totalShots,
                                 shotsOnTarget, foulsCommitted, offsides
        nba     (Jayson Tatum):  points, totalRebounds, assists, ...
        nfl     (Josh Allen):    passingYards, passingTouchdowns, ...

    mlb -> statsapi.mlb.com
        /people/{id}/stats?stats=gameLog&group=hitting
        (hits, homeRuns, strikeOuts, totalBases, runs, rbi, stolenBases)

Regla dura: la racha se calcula SOLO con partidos ya jugados ANTES del
partido de la prop (`fecha < fecha_evento`). Incluir el propio partido
convertiria el porcentaje en una mentira.

Si no se encuentra al jugador o la fuente no responde, se devuelve
`{"encontrado": False, "motivo": ...}`. Nunca se rellena con numeros
inventados.
"""

import time
import unicodedata
from datetime import datetime

import requests

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_COMMON = "https://site.web.api.espn.com/apis/common/v3/sports"
MLB_BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 20
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 3SIXTYBETS/1.0)"}

# Ruta de ESPN por deporte. El futbol depende de la liga (soccer/esp.1...).
_ESPN_PATH = {
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
}
DEPORTES = ("soccer", "nba", "nfl", "mlb")

# Cache en memoria (el scheduler corre cada 90 min; 6 h es de sobra).
_CACHE = {}
_CACHE_TTL = 6 * 3600
_MAX_CACHE = 4000


def _cache_get(clave):
    item = _CACHE.get(clave)
    if not item:
        return None
    ts, valor = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(clave, None)
        return None
    return valor


def _cache_set(clave, valor):
    if len(_CACHE) > _MAX_CACHE:
        _CACHE.clear()
    _CACHE[clave] = (time.time(), valor)


def cache_clear():
    """Limpia el cache (admin / tests)."""
    _CACHE.clear()


# --------------------------------------------------------------- utilidades


def _norm(texto) -> str:
    """Minusculas, sin acentos y sin puntuacion (para comparar nombres)."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", " ").replace("-", " ").replace("'", " ")
    return " ".join(s.split())


def _num(valor):
    """Numero a partir de los strings de ESPN ('24', '3.6', '10-20' -> None)."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        pass
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _get_json(url, params=None):
    try:
        r = requests.get(url, params=params or {}, headers=_HEADERS, timeout=TIMEOUT)
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def _fecha(valor) -> str:
    """'2026-10-24T16:30:00.000+00:00' -> '2026-10-24'."""
    return str(valor or "")[:10]


def _ruta_espn(sport: str, league: str | None):
    """Ruta de ESPN del deporte ('soccer/esp.1', 'basketball/nba'...)."""
    if sport == "soccer":
        return f"soccer/{league}" if league else None
    return _ESPN_PATH.get(sport)


def _foto(sport: str, player_id, del_roster=None):
    """URL publica de la cara del jugador (comprobadas 200 el 30/10/2026)."""
    if del_roster:
        return del_roster
    if not player_id:
        return None
    if sport == "mlb":
        return (
            "https://img.mlbstatic.com/mlb-photos/image/upload/"
            "d_people:generic:headshot:67:current.png/w_213,q_auto:best/"
            f"v1/people/{player_id}/headshot/67/current"
        )
    liga = {"soccer": "soccer", "nba": "nba", "nfl": "nfl"}.get(sport, sport)
    return f"https://a.espncdn.com/i/headshots/{liga}/players/full/{player_id}.png"


def _extraer_url(valor):
    """ESPN devuelve headshot como {'href': url} (o lista de ellos)."""
    if isinstance(valor, dict):
        valor = valor.get("href")
    elif isinstance(valor, list):
        primero = valor[0] if valor else None
        valor = primero.get("href") if isinstance(primero, dict) else primero
    if isinstance(valor, str) and valor.startswith("http"):
        return valor
    return None



# ------------------------------------------------------------ roster (ESPN)


def _aplanar_roster(athletes) -> list:
    """ESPN devuelve el roster de dos formas:

        NBA/MLB/futbol: lista plana de jugadores
        NFL:            lista de grupos {'position': 'offense', 'items': [...]}

    Aqui se devuelve siempre una lista plana de jugadores.
    """
    salida = []
    for item in athletes or []:
        if not isinstance(item, dict):
            continue
        if item.get("items"):
            salida.extend(_aplanar_roster(item.get("items")))
        elif item.get("id"):
            salida.append(item)
    return salida


def roster(sport: str, league: str | None, team_id):
    """Jugadores del equipo segun ESPN (cache 6 h)."""
    if not team_id:
        return []
    ruta = _ruta_espn(sport, league)
    if not ruta:
        return []
    clave = f"roster:{ruta}:{team_id}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get_json(f"{ESPN_SITE}/{ruta}/teams/{team_id}/roster")
    jugadores = _aplanar_roster((data or {}).get("athletes"))
    if jugadores:
        _cache_set(clave, jugadores)
    return jugadores


def buscar_jugador(jugadores: list, nombre: str):
    """Encuentra al jugador del book dentro del roster de ESPN.

    El book escribe 'Gerard Moreno' y ESPN 'Gerard Moreno'; pero tambien
    aparecen diferencias ('Roberto Fernandez Jaen' vs 'Roberto Fernandez').
    Se prueba de mas a menos estricto y nunca se adivina entre varios.
    """
    objetivo = _norm(nombre)
    if not objetivo or not jugadores:
        return None

    def _nombre(j):
        return _norm(j.get("fullName") or j.get("displayName") or "")

    # 1) exacto
    for j in jugadores:
        if _nombre(j) == objetivo:
            return j
    # 2) el nombre del book esta contenido en el de ESPN o al reves
    candidatos = [j for j in jugadores
                  if objetivo in _nombre(j) or (_nombre(j) and _nombre(j) in objetivo)]
    if len(candidatos) == 1:
        return candidatos[0]
    # 3) nombre + primer apellido
    partes = objetivo.split()
    if len(partes) >= 2:
        corto = f"{partes[0]} {partes[-1]}"
        candidatos = [j for j in jugadores if corto in _nombre(j)]
        if len(candidatos) == 1:
            return candidatos[0]
    # 4) solo el apellido, y solo si es unico en el equipo
    if partes:
        apellido = partes[-1]
        if len(apellido) >= 4:
            candidatos = [j for j in jugadores if apellido in _nombre(j).split()]
            if len(candidatos) == 1:
                return candidatos[0]
    return None



# ------------------------------------------------- gameLog (ESPN y statsapi)


def _gamelog_espn(sport: str, league: str | None, player_id):
    """Todos los partidos del jugador segun ESPN, mas reciente primero."""
    ruta = _ruta_espn(sport, league)
    if not ruta or not player_id:
        return []
    clave = f"gamelog:{ruta}:{player_id}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    data = _get_json(f"{ESPN_COMMON}/{ruta}/athletes/{player_id}/gamelog")
    if not data:
        return []
    nombres = [str(n) for n in (data.get("names") or [])]
    meta = data.get("events") or {}
    salida, vistos = [], set()
    for season in data.get("seasonTypes") or []:
        for cat in season.get("categories") or []:
            for ev in cat.get("events") or []:
                eid = str(ev.get("eventId") or "")
                if not eid or eid in vistos:
                    continue
                vistos.add(eid)
                valores = ev.get("stats") or []
                stats = {}
                for i, campo in enumerate(nombres):
                    if i < len(valores):
                        valor = _num(valores[i])
                        if valor is not None:
                            stats[campo] = valor
                info = meta.get(eid) or {}
                oponente = info.get("opponent") or {}
                salida.append({
                    "evento_id": eid,
                    "fecha": _fecha(info.get("gameDate")),
                    "rival": (oponente.get("displayName")
                              or oponente.get("abbreviation") or None),
                    "casa": (info.get("atVs") == "vs"),
                    "stats": stats,
                })
    salida.sort(key=lambda x: x["fecha"] or "", reverse=True)
    if salida:
        _cache_set(clave, salida)
    return salida


def _mlb_persona(nombre: str):
    """playerId de statsapi a partir del nombre ('Kyle Schwarber' -> 656941)."""
    clave = f"mlb_persona:{_norm(nombre)}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get_json(f"{MLB_BASE}/people/search", {"names": nombre})
    people = (data or {}).get("people") or []
    if not people:
        return None
    objetivo = _norm(nombre).split()
    elegido = people[0]
    if objetivo:
        apellido = objetivo[-1]
        for p in people:
            if apellido and apellido in _norm(p.get("fullName") or "").split():
                elegido = p
                break
    pid = elegido.get("id")
    if pid:
        _cache_set(clave, pid)
    return pid


def _gamelog_mlb(player_id, n: int):
    """Partidos MLB del jugador (hitting o pitching, el que tenga datos)."""
    if not player_id:
        return []
    clave = f"gamelog:mlb:{player_id}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    salida = []
    anio = datetime.now().year
    for temporada in (anio, anio - 1):
        for grupo in ("hitting", "pitching"):
            data = _get_json(
                f"{MLB_BASE}/people/{player_id}/stats",
                {"stats": "gameLog", "group": grupo, "season": temporada},
            )
            splits = []
            for bloque in (data or {}).get("stats") or []:
                splits.extend(bloque.get("splits") or [])
            for s in splits:
                stat = s.get("stat") or {}
                stats = {}
                for campo, valor in stat.items():
                    numero = _num(valor)
                    if numero is not None:
                        stats[campo] = numero
                salida.append({
                    "evento_id": s.get("gamePk"),
                    "fecha": _fecha(s.get("date")),
                    "rival": ((s.get("opponent") or {}).get("name") or None),
                    "casa": bool(s.get("isHome")),
                    "stats": stats,
                })
        if len(salida) >= n:
            break
    # Quedarse solo con los partidos que traen los campos utiles (un pitcher
    # no tiene 'hits' de bateo y viceversa).
    salida = [s for s in salida if s.get("fecha")]
    salida.sort(key=lambda x: x["fecha"], reverse=True)
    if salida:
        _cache_set(clave, salida)
    return salida



# ------------------------------------------------------------- API publica


def _equipo_de_evento(evento, equipo_abbr):
    """Equipo del evento ESPN que corresponde al tag del book ('VIL', 'WAS').

    El book identifica al jugador por su equipo; ESPN trae la abreviatura del
    equipo en el propio evento, asi que el match es directo (verificado:
    VIL, ALA, BAR, ATM, WAS, CHA, PIT...).
    """
    abbr = str(equipo_abbr or "").upper().strip()
    if not abbr:
        return None
    for lado in ("home", "away"):
        team = (evento or {}).get(lado) or {}
        if str(team.get("abbr") or "").upper() == abbr:
            return team
    return None


def partidos_jugador(sport: str, league, player_id) -> list:
    """Todos los partidos conocidos del jugador, sin filtrar por fecha.

    Lo usa el cierre de props para localizar el partido concreto del evento
    (misma fecha) y sacar el valor real que consiguio.
    """
    if not player_id:
        return []
    if sport == "mlb":
        return _gamelog_mlb(player_id, 60)
    return _gamelog_espn(sport, league, player_id)


def historial_jugador(sport: str, jugador: str, equipo: str, evento: dict, n: int = 5):
    """Ultimos n partidos del jugador JUGADOS ANTES del evento.

    `evento` es el dict de sports.get_sport_games (home/away/date/league_code).
    `equipo` es el tag del book ('VIL'), que sirve para localizar al jugador.

    Devuelve:
        {"encontrado": bool, "id", "nombre", "foto",
         "partidos": [{"fecha", "stats"}, ...]}   # antiguo -> reciente

    Los partidos vienen en orden cronologico (izquierda a derecha en el
    grafico de barras). Si no hay historico se devuelve `partidos: []`; nunca
    valores inventados.
    """
    if sport not in DEPORTES:
        return {"encontrado": False, "motivo": f"deporte no soportado: {sport}"}
    if not jugador:
        return {"encontrado": False, "motivo": "sin nombre de jugador"}

    evento = evento or {}
    fecha_evento = _fecha(evento.get("date"))
    league = evento.get("league_code")

    if sport == "mlb":
        pid = _mlb_persona(jugador)
        if not pid:
            return {"encontrado": False, "motivo": "jugador no esta en statsapi"}
        partidos = _gamelog_mlb(pid, n)
        foto, nombre = _foto("mlb", pid), jugador
    else:
        # Primero el equipo que indica el book; si no aparece, el otro.
        candidatos = []
        equipo_book = _equipo_de_evento(evento, equipo)
        if equipo_book:
            candidatos.append(equipo_book)
        for lado in ("home", "away"):
            team = evento.get(lado) or {}
            if team and team not in candidatos:
                candidatos.append(team)

        pid = foto = nombre = None
        partidos = []
        for team in candidatos:
            encontrado = buscar_jugador(roster(sport, league, team.get("id")), jugador)
            if not encontrado:
                continue
            pid = encontrado.get("id")
            foto = _extraer_url(encontrado.get("headshot")) or _foto(sport, pid)
            nombre = encontrado.get("fullName") or encontrado.get("displayName") or jugador
            partidos = _gamelog_espn(sport, league, pid)
            break
        if not pid:
            return {
                "encontrado": False,
                "motivo": "jugador no encontrado en el roster de ESPN",
            }

    anteriores = []
    for p in partidos:
        fecha = p.get("fecha") or ""
        # Anti-leakage: el partido de la prop (y cualquier posterior) queda fuera.
        if fecha_evento and fecha and fecha >= fecha_evento:
            continue
        if not p.get("stats"):
            continue
        anteriores.append(p)
    anteriores = anteriores[:n]

    return {
        "encontrado": True,
        "id": pid,
        "nombre": nombre,
        "foto": foto,
        "partidos": list(reversed(anteriores)),  # antiguo -> reciente
    }

