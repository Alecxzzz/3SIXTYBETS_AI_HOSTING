"""
Verificacion de ENTIDADES deportivas: jugador -> equipo real, y equipo ->
competicion. Fuentes OFICIALES/ESTRUCTURADAS y gratuitas (sin API key).

Problema real que resuelve (bug reportado):
    El modelo alucinaba "Texas Rangers (Tyler Mahle) vs Philadelphia Phillies".
    Tyler Mahle NO juega en Texas Rangers. El modelo se inventaba el roster
    porque solo tenia busqueda web libre, sin fuente de verdad de plantilla
    ni de calendario.

Fuentes usadas:
  - ESPN search API   -> resuelve nombre -> equipo, liga y deporte REALES
  - statsapi.mlb.com  -> plantilla activa de MLB, calendario y partido real
  - ESPN site v2      -> plantilla y record de cualquier liga (NBA/NFL/NHL/fut)
"""

import os
import re
import threading
import time
import unicodedata

import requests

ESPN_SEARCH_URL = "https://site.api.espn.com/apis/search/v2"
# site.api.espn.com devuelve 403 de forma intermitente. El mismo buscador
# responde en site.web.api.espn.com, asi que se prueban los dos hosts.
ESPN_SEARCH_HOSTS = [
    ESPN_SEARCH_URL,
    "https://site.web.api.espn.com/apis/search/v2",
]
ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_SITE_MIRROR = "https://site.web.api.espn.com/apis/site/v2/sports"
MLB_BASE = "https://statsapi.mlb.com/api/v1"

TIMEOUT = 12
# ESPN bloquea (403) las peticiones con User-Agent corto/de bot: hay que
# enviar un UA de navegador completo o el buscador responde Access Denied.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}

DEPORTE_PATH = {
    "baseball": "baseball",
    "basketball": "basketball",
    "football": "football",
    "hockey": "hockey",
    "soccer": "soccer",
    "tennis": "tennis",
    "golf": "golf",
    "mma": "mma",
    "racing": "racing",
}

DEPORTE_ES = {
    "baseball": "beisbol",
    "basketball": "basquetbol",
    "football": "futbol americano",
    "hockey": "hockey sobre hielo",
    "soccer": "futbol",
    "tennis": "tenis",
    "golf": "golf",
}

LIGA_ES = {
    "mlb": "MLB (Major League Baseball, beisbol de utmost nivel de USA)",
    "nba": "NBA (basquetbol profesional de USA)",
    "wnba": "WNBA (basquetbol femenino de USA)",
    "nfl": "NFL (futbol americano profesional de USA)",
    "nhl": "NHL (hockey sobre hielo profesional de USA)",
    "atp": "ATP (tenis masculino profesional)",
    "wta": "WTA (tenis femenino profesional)",
    "mls": "MLS (futbol de Estados Unidos y Canada)",
    "uefa.champions": "UEFA Champions League",
    "uefa.europa": "UEFA Europa League",
    "eng.1": "Premier League (Inglaterra)",
    "esp.1": "LaLiga (Espana)",
    "ita.1": "Serie A (Italia)",
    "ger.1": "Bundesliga (Alemania)",
    "fra.1": "Ligue 1 (Francia)",
    "por.1": "Liga Portugal",
    "ned.1": "Eredivisie (Paises Bajos)",
}

_CACHE = {}
_CACHE_TTL = 900  # 15 min: la plantilla no cambia cada minuto


def _norm(texto):
    """Minusculas y sin acentos, para comparar nombres de forma tolerante."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", str(texto).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def _cache_get(clave):
    dato = _CACHE.get(clave)
    if dato and (time.time() - dato[0]) < _CACHE_TTL:
        return dato[1]
    return None


def _cache_put(clave, valor):
    _CACHE[clave] = (time.time(), valor)
    return valor


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def _espn_site_json(sport_path, liga, ruta, params=None):
    """site v2 de ESPN probando el host principal y el mirror."""
    for base in (ESPN_SITE_BASE, ESPN_SITE_MIRROR):
        try:
            r = requests.get(
                f"{base}/{sport_path}/{liga}/{ruta}",
                params=params, headers=_HEADERS, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None



def _search_espn(consulta, tipo=None, limite=6):
    """Busqueda de ESPN: convierte los resultados en dicts planos."""
    clave = f"search:{tipo}:{_norm(consulta)}:{limite}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    params = {"query": consulta, "limit": limite}
    data = None
    for host in ESPN_SEARCH_HOSTS:
        data = _get(host, params=params)
        if data:
            break
    if not data:
        return _cache_put(clave, [])

    encontrados = []
    for bloque in data.get("results", []) or []:
        for item in bloque.get("contents", []) or []:
            uid = item.get("uid") or ""
            # uid = s:<sport>~l:<liga>~a:<jugador>  (o ~t:<equipo>)
            partes = dict(p.split(":", 1) for p in uid.split("~") if ":" in p)
            encontrados.append({
                "tipo": item.get("type") or bloque.get("type"),
                "id": (partes.get("a") or partes.get("t") or "").strip() or item.get("id"),
                "nombre": item.get("displayName") or "",
                "equipo": item.get("subtitle") or "",
                "competicion": item.get("description")
                or item.get("defaultLeagueSlug") or "",
                "deporte": item.get("sport") or partes.get("s") or "",
                "liga": item.get("defaultLeagueSlug") or partes.get("l") or "",
                "liga_id": partes.get("l") or "",
                "link": ((item.get("link") or {}).get("web") or ""),
            })
    return _cache_put(clave, encontrados)


def _puntuar(nombre_real, nombre_buscado):
    """Similitud simple: que fraccion del nombre buscado aparece en el real."""
    a, b = _norm(nombre_real), _norm(nombre_buscado)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.9
    partes_b = [p for p in b.split() if len(p) >= 3]
    if not partes_b:
        return 0.0
    return sum(1 for p in partes_b if p in a) / len(partes_b)


def buscar_jugador(nombre):
    """
    Resuelve un jugador -> equipo, liga y deporte REALES.

    Para MLB se usa statsapi.mlb.com (fuente OFICIAL, sin rate limit) y su
    campo `currentTeam`, que es la verdad de a quien pertenece hoy. ESPN
    search sirve de respaldo para otros deportes, pero se cae a 403 con
    facilidad, por eso va despues.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        return None

    # 1) Fuente oficial de MLB.
    oficial = _buscar_jugador_mlb(nombre)
    if oficial:
        return oficial

    # 2) Respaldo: buscador de ESPN (NBA, NFL, NHL, futbol, tenis).
    resultados = [
        r for r in _search_espn(nombre)
        if r.get("tipo") == "player" and r.get("nombre")
    ]
    if not resultados:
        return None
    resultados.sort(key=lambda r: _puntuar(r["nombre"], nombre), reverse=True)
    mejor = dict(resultados[0])
    mejor["similitud"] = round(_puntuar(mejor["nombre"], nombre), 2)
    return mejor


def _buscar_jugador_mlb(nombre):
    """statsapi: nombre -> jugador con su currentTeam oficial."""
    clave = f"mlb:people:{_norm(nombre)}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado or None

    data = _get(f"{MLB_BASE}/people/search", params={"names": nombre})
    encontrados = []
    for p in (data or {}).get("people", []) or []:
        if not p.get("fullName"):
            continue
        # /people/search NO trae currentTeam: hay que hidratar el detalle.
        equipo = p.get("currentTeam") or {}
        if not equipo and p.get("id"):
            detalle = _get(f"{MLB_BASE}/people/{p['id']}",
                           params={"hydrate": "currentTeam,primaryPosition"})
            for d in ((detalle or {}).get("people") or [])[:1]:
                equipo = d.get("currentTeam") or {}
                p = {**p, **(d or {})}
        encontrados.append({
            "tipo": "player",
            "id": p.get("id"),
            "nombre": p.get("fullName"),
            "equipo": equipo.get("name") or None,
            "equipo_id": equipo.get("id"),
            "competicion": "MLB",
            "deporte": "baseball",
            "liga": "mlb",
            "posicion": ((p.get("primaryPosition") or {}).get("abbreviation")),
            "activo": p.get("active", True),
        })

    mejor = None
    if encontrados:
        encontrados.sort(key=lambda r: _puntuar(r["nombre"], nombre), reverse=True)
        mejor = dict(encontrados[0])
        mejor["similitud"] = round(_puntuar(mejor["nombre"], nombre), 2)
    return _cache_put(clave, mejor)


def buscar_equipo(nombre, liga=None):
    """
    Resuelve un equipo -> id, liga y deporte REALES.

    Primero la tabla de MLB (fuente oficial, siempre disponible); despues el
    buscador de ESPN para el resto de deportes.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        return None

    tid = mlb_team_id(nombre)
    if tid:
        datos = _mapa_teams().get(tid, {})
        return {
            "tipo": "team",
            "id": tid,
            "nombre": datos.get("nombre") or nombre,
            "competicion": datos.get("liga") or _nombre_liga("mlb"),
            "deporte": "baseball",
            "liga": "mlb",
            "similitud": 1.0,
        }

    resultados = [
        r for r in _search_espn(nombre)
        if r.get("tipo") == "team" and r.get("nombre")
    ]
    if not resultados:
        return None
    if liga:
        resultados = [r for r in resultados
                      if (r.get("liga") or "").lower() == liga.lower()] or resultados
    resultados.sort(key=lambda r: _puntuar(r["nombre"], nombre), reverse=True)
    mejor = dict(resultados[0])
    mejor["similitud"] = round(_puntuar(mejor["nombre"], nombre), 2)
    return mejor



# ------------------------------------------------------------- MLB (statsapi)

def _mapa_teams():
    """id -> datos de TODOS los equipos de MLB (nombre, liga, division)."""
    cacheado = _cache_get("mlb:teams")
    if cacheado:
        return cacheado
    data = _get(f"{MLB_BASE}/teams", params={"sportId": 1})
    mapa = {}
    if data:
        for t in data.get("teams", []):
            # OJO: la API devuelve `league` y `division` como OBJETOS
            # ({id, name, link}), no como cadenas. Leer "leagueName" daria
            # None y despues no se podria ubicar al equipo en su tabla.
            liga = t.get("league") or {}
            liga_nombre = liga.get("name") if isinstance(liga, dict) else liga
            liga_id = liga.get("id") if isinstance(liga, dict) else None
            division = t.get("division")
            if isinstance(division, dict):
                division = division.get("name")
            mapa[str(t.get("id"))] = {
                "id": t.get("id"),
                "nombre": t.get("name"),
                "abbrev": t.get("abbreviation") or t.get("abbrev"),
                "franquicia": t.get("franchiseName"),
                "liga": liga_nombre,
                "liga_id": liga_id,
                "division": division,
                "deporte": (t.get("sport") or {}).get("name"),
                "venue": (t.get("venue") or {}).get("name"),
            }
    return _cache_put("mlb:teams", mapa)


def mlb_team_id(nombre):
    """Id de MLB de un equipo por nombre (coincidencia tolerante por tokens)."""
    mapa = _mapa_teams()
    if not mapa or not nombre:
        return None
    objetivo = _norm(nombre)
    mejor, mejor_score = None, 0.0
    for tid, info in mapa.items():
        for campo in ("nombre", "franquicia", "abbrev"):
            valor = _norm(info.get(campo) or "")
            if not valor:
                continue
            score = _puntuar(valor, objetivo)
            if objetivo in valor or valor in objetivo:
                score = max(score, 0.85)
            if score > mejor_score:
                mejor, mejor_score = tid, score
    return mejor if mejor_score >= 0.6 else None


def mlb_roster(team_id, temporada=None, roster_type="fullSeason"):
    """
    Plantilla de un equipo de MLB.

    `activeSeason` viene TRUNCADA (26 jugadores) y genera falsos negativos al
    confirmar si un jugador sigue en el equipo, asi que por defecto se usa
    `fullSeason`, que trae la plantilla completa de la temporada.
    """
    clave = f"mlb:roster:{team_id}:{temporada}:{roster_type}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    params = {"rosterType": roster_type}
    if temporada:
        params["season"] = temporada
    data = _get(f"{MLB_BASE}/teams/{team_id}/roster", params=params)
    nombres = []
    if data:
        for entrada in data.get("roster", []) or []:
            persona = entrada.get("person") or {}
            if persona.get("fullName"):
                nombres.append({
                    "id": persona.get("id"),
                    "nombre": persona.get("fullName"),
                    "posicion": (entrada.get("position") or {}).get("abbreviation"),
                })
    return _cache_put(clave, nombres)


def mlb_partidos_fecha(team_id, fecha_iso):
    """Rivales REALES de un equipo de MLB en una fecha (YYYY-MM-DD)."""
    clave = f"mlb:sched:{team_id}:{fecha_iso}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/schedule",
                params={"sportId": 1, "teamId": team_id, "date": fecha_iso})
    salida = []
    if data:
        for dia in data.get("dates", []) or []:
            for juego in dia.get("games", []) or []:
                teams = juego.get("teams", {}) or {}
                away = (teams.get("away") or {}).get("team", {}) or {}
                home = (teams.get("home") or {}).get("team", {}) or {}
                salida.append({
                    "gamePk": juego.get("gamePk"),
                    "visitante": away.get("name"),
                    "local": home.get("name"),
                    "fecha": juego.get("officialDate") or fecha_iso,
                    "hora": juego.get("gameDate"),
                    "estado": ((juego.get("status") or {}).get("detailedState") or ""),
                })
    return _cache_put(clave, salida)


# ------------------------------------------- standings / contexto competitivo

def mlb_standings(league_id, temporada, tipo="byLeague"):
    """Tabla de posiciones de una liga de MLB, con estado de clasificacion."""
    clave = f"mlb:standings:{league_id}:{temporada}:{tipo}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/standings", params={
        "leagueId": league_id, "season": temporada, "standingTypes": tipo,
    })
    filas = []
    for grupo in (data or {}).get("records", []) or []:
        for rec in grupo.get("teamRecords", []) or []:
            equipo = rec.get("team") or {}
            splits = {}
            for s in (rec.get("records") or {}).get("splitRecords", []) or []:
                splits[s.get("type")] = s
            filas.append({
                "id": equipo.get("id"),
                "nombre": equipo.get("name"),
                "wins": rec.get("wins"),
                "losses": rec.get("losses"),
                "pct": rec.get("winningPercentage"),
                "divisionRank": rec.get("divisionRank"),
                "leagueRank": rec.get("leagueRank"),
                "gamesBack": rec.get("gamesBack"),
                "wildCardGamesBack": rec.get("wildCardGamesBack"),
                "lastTen": (
                    f"{splits['lastTen']['wins']}-{splits['lastTen']['losses']}"
                    if "lastTen" in splits else None
                ),
                "home": (
                    f"{splits['home']['wins']}-{splits['home']['losses']}"
                    if "home" in splits else None
                ),
                "away": (
                    f"{splits['away']['wins']}-{splits['away']['losses']}"
                    if "away" in splits else None
                ),
                "runDiff": rec.get("runDifferential"),
                "clinched": rec.get("clinched"),
                "divisionLeader": rec.get("divisionLeader"),
                "divisionChamp": rec.get("divisionChamp"),
                "eliminacion": rec.get("wildCardEliminationNumber"),
                "streak": (rec.get("streak") or {}).get("streakCode"),
            })
    return _cache_put(clave, filas)

def mlb_lanzadores_titulares(game_pk):
    """Lanzadores titulares CONFIRMADOS de un partido de MLB (boxscore)."""
    if not game_pk:
        return []
    clave = f"mlb:box:{game_pk}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/game/{game_pk}/boxscore")
    titulares = []
    if data:
        for lado in ("away", "home"):
            equipo = ((data.get("teams") or {}).get(lado) or {})
            for _pid, info in (equipo.get("players") or {}).items():
                posicion = ((info.get("position") or {}).get("abbreviation") or "")
                bateo = ((info.get("stats") or {}).get("battingOrder") or "")
                # El lanzador titular es el que bate 1 en el orden de bateo
                if posicion == "P" and str(bateo) in ("1", "1.0"):
                    titulares.append({
                        "equipo": (equipo.get("team") or {}).get("name"),
                        "nombre": (info.get("person") or {}).get("fullName"),
                        "posicion": posicion,
                    })
    return _cache_put(clave, titulares)


def stance_mlb(nombre_equipo, temporada):
    """
    Fila de la tabla de un equipo de MLB con su situacion de MOTIVACION:
    por que ese partido importa (titulo, playoffs, eliminacion o nada en juego).
    """
    tid = mlb_team_id(nombre_equipo)
    if not tid:
        return None
    mapa = _mapa_teams().get(str(tid), {})
    # league_id viene directo del endpoint de equipos (103 = AL, 104 = NL).
    league_id = mapa.get("liga_id")
    if not league_id:
        league_id = {"american league": "103", "national league": "104"}.get(
            (mapa.get("liga") or "").lower()
        )
    if not league_id:
        return None

    filas = mlb_standings(league_id, temporada)
    fila = next((f for f in filas if str(f.get("id")) == str(tid)), None)
    if not fila:
        return None

    ordenadas = sorted(
        filas, key=lambda f: (f.get("wins") or 0), reverse=True
    )
    fila["posicion_real"] = ordenadas.index(fila) + 1
    fila["total_equipos"] = len(ordenadas)
    fila["lider"] = ordenadas[0]
    return fila


def motivation_text(estado):
    """
    Traduce la situacion de tabla a lenguaje de motivacion. Esto es lo que
    le dice al modelo POR QUE ese partido importa de verdad.
    """
    if not estado:
        return ""
    partes = []

    pos = estado.get("posicion_real")
    total = estado.get("total_equipos")
    if pos and total:
        partes.append(f"Posicion {pos} de {total} en su liga.")

    if estado.get("divisionChamp"):
        partes.append("Ya GANO su division: juega por asegurar el primer puesto.")
    elif estado.get("divisionLeader"):
        partes.append("Va LIDER de la division: defender la ventaja es la motivacion.")
    elif estado.get("clinched"):
        partes.append(
            "Ya esta CLASIFICADO a playoffs: el objetivo ahora es llegar fuerte."
        )

    elim = estado.get("eliminacion")
    # La API usa "-" si sigue vivo y "E" (eliminados) si ya no hay retorno.
    if elim in ("E", "e", "ELIMINATED"):
        partes.append(
            "Esta ELIMINADO: ya no hay nada en juego. Se suelen rotar las "
            "bajas y baja el rendimiento, con mas tendencia a perder por margen."
        )
    elif elim not in ("-", "", None):
        try:
            n = float(elim)
            if n <= 10:
                partes.append(
                    f"A {elim} juegos de la eliminacion: esta en el limite EXACTO, "
                    "cada partido es de vida o muerte."
                )
            else:
                partes.append(f"A {elim} juegos de la eliminacion: aun con margen.")
        except (TypeError, ValueError):
            partes.append(f"Eliminacion a {elim} juegos.")

    gb = estado.get("gamesBack")
    lider = estado.get("lider") or {}
    eliminado = elim in ("E", "e", "ELIMINATED")
    if not eliminado and gb not in ("-", "", None) and lider:
        try:
            n = float(gb)
            if n <= 3:
                partes.append(
                    f"Solo {gb} juegos por detras del lider ({lider.get('nombre')}, "
                    f"{lider.get('wins')}-{lider.get('losses')}): pelea por el titulo abierta."
                )
            elif n <= 10:
                partes.append(f"A {gb} juegos del lider: pelea por puesto de honor.")
            else:
                partes.append(
                    f"A {gb} juegos del lider: el objetivo realista son los playoffs."
                )
        except (TypeError, ValueError):
            pass

    racha = estado.get("streak")
    if racha:
        tipo = "victorias" if str(racha).startswith("W") else "derrotas"
        partes.append(f"Viene de {racha} ({tipo} seguidas).")

    return " ".join(partes)

    """Lanzadores titulares CONFIRMADOS de un partido de MLB (boxscore)."""
    if not game_pk:
        return []
    clave = f"mlb:box:{game_pk}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/game/{game_pk}/boxscore")
    titulares = []
    if data:
        for lado in ("away", "home"):
            equipo = ((data.get("teams") or {}).get(lado) or {})
            for _pid, info in (equipo.get("players") or {}).items():
                posicion = ((info.get("position") or {}).get("abbreviation") or "")
                bateo = ((info.get("stats") or {}).get("battingOrder") or "")
                # El lanzador titular es el que bate 1 en el orden de bateo
                if posicion == "P" and str(bateo) in ("1", "1.0"):
                    titulares.append({
                        "equipo": (equipo.get("team") or {}).get("name"),
                        "nombre": (info.get("person") or {}).get("fullName"),
                        "posicion": posicion,
                    })
    return _cache_put(clave, titulares)


# ------------------------------------------- roster genérico ESPN (otras ligas)

def espn_roster(deporte_path, liga, team_id, limite=60):
    """Nombres del roster de un equipo en cualquier liga cubierta por ESPN."""
    clave = f"espn:roster:{deporte_path}:{liga}:{team_id}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _espn_site_json(deporte_path, liga, f"teams/{team_id}/roster")
    nombres = []
    if data:
        for grupo in data.get("athletes", []) or []:
            for item in grupo.get("items", []) or []:
                if not item.get("fullName"):
                    continue
                posicion = item.get("position")
                if isinstance(posicion, dict):
                    posicion = posicion.get("abbreviation")
                nombres.append({
                    "id": item.get("id"),
                    "nombre": item.get("fullName"),
                    "posicion": posicion,
                })
    return _cache_put(clave, nombres[:limite])


def espn_team_info(deporte_path, liga, team_id):
    """Nombre, record y posición de tabla de un equipo."""
    clave = f"espn:team:{deporte_path}:{liga}:{team_id}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _espn_site_json(deporte_path, liga, f"teams/{team_id}")
    info = {}
    if data:
        t = data.get("team", {}) or {}
        items = (t.get("record") or {}).get("items", []) or []
        overall = next((r.get("summary") for r in items if r.get("type") == "total"), None)
        info = {
            "id": team_id,
            "nombre": t.get("displayName"),
            "siglas": t.get("abbreviation"),
            "record": overall,
            "tabla": t.get("standingSummary"),
            "ciudad": t.get("location"),
        }
    return _cache_put(clave, info)


def _nombre_liga(liga_slug, descripcion=""):
    if liga_slug and liga_slug in LIGA_ES:
        return LIGA_ES[liga_slug]
    return descripcion or liga_slug or "competicion no identificada"


def verificar_jugador_en_equipo(nombre_jugador, nombre_equipo=None):
    """
    Verifica con fuente real si un jugador PERTENECE a un equipo.

    Claves: encontrado, jugador, equipo_real, competicion,
    pertenece (True/False/None si no se pudo confirmar) y motivo.
    """
    res = {"encontrado": False, "jugador": None, "equipo_real": None,
           "competicion": None, "pertenece": None, "motivo": ""}

    jugador = buscar_jugador(nombre_jugador)
    if not jugador:
        res["motivo"] = (
            f"No se encontro al jugador '{nombre_jugador}' en la base de datos. "
            "No lo incluyas como si fuera titular confirmado."
        )
        return res
    if _norm(jugador.get("nombre")) != _norm(nombre_jugador) \
            and _puntuar(jugador.get("nombre"), nombre_jugador) < 0.6:
        res["motivo"] = (
            f"El nombre '{nombre_jugador}' no coincide exactamente con nadie en la base "
            f"de datos. No lo des como titular confirmado."
        )
        return res

    res["encontrado"] = True
    res["jugador"] = jugador["nombre"]
    res["equipo_real"] = jugador.get("equipo") or None
    res["competicion"] = _nombre_liga(jugador.get("liga"), jugador.get("competicion"))

    # Doble confirmacion contra la plantilla activa oficial (MLB).
    liga = (jugador.get("liga") or "").lower()
    if liga == "mlb" and res["equipo_real"]:
        tid = mlb_team_id(res["equipo_real"])
        if tid:
            roster = mlb_roster(tid)
            partes_nombre = _norm(jugador["nombre"]).split()
            apellido = partes_nombre[-1] if partes_nombre else ""
            confirmado = any(
                _norm(n["nombre"]) == _norm(jugador["nombre"])
                or (apellido and _norm(n["nombre"]).split()[-1:] == [apellido])
                for n in roster
            )
            if not confirmado:
                res["motivo"] = (
                    f"{jugador['nombre']} aparece vinculado a {res['equipo_real']} en el "
                    "buscador, pero NO figura en su plantilla activa oficial: puede "
                    "haber sido cambiado de equipo. No lo des como titular confirmado."
                )
                return res

    if nombre_equipo:
        objetivo = _norm(nombre_equipo)
        real = _norm(res["equipo_real"] or "")
        coincide = bool(real) and (
            objetivo in real or real in objetivo
            or _puntuar(real, objetivo) >= 0.6
            or _puntuar(objetivo, real) >= 0.6
        )
        res["pertenece"] = coincide
        if not coincide:
            res["motivo"] = (
                f"ERROR DE PLANTILLA: {jugador['nombre']} juega en "
                f"{res['equipo_real']}, NO en {nombre_equipo}. "
                f"Competicion real: {res['competicion']}."
            )
    return res


def contexto_equipo(nombre_equipo, fecha_iso=None):
    """
    Ficha REAL y profunda de un equipo: competicion, division, record, tabla,
    plantilla activa, partido real de la fecha y lanzadores titulares.
    """
    ctx = {
        "encontrado": False, "nombre": None, "competicion": None, "liga": None,
        "deporte": None, "record": None, "tabla": None, "division": None,
        "plantilla": [], "partido_real": None, "rival_real": None, "rival_id": None,
        "notas": [], "id": None,
    }

    tid = mlb_team_id(nombre_equipo)
    if tid:
        datos = _mapa_teams().get(tid, {})
        info = {
            "id": int(tid),
            "nombre": datos.get("nombre"),
            # La liga del endpoint es "American/National League": se
            # expande a la descripcion completa de MLB.
            "competicion": _nombre_liga("mlb", datos.get("liga")),
            "liga_slug": "mlb",
            "deporte": "baseball",
            "division": datos.get("division"),
        }
    else:
        eq = buscar_equipo(nombre_equipo)
        if not eq:
            ctx["notas"].append(f"No se pudo identificar el equipo '{nombre_equipo}'.")
            return ctx
        info = {
            "id": eq.get("id"),
            "nombre": eq.get("nombre"),
            "competicion": _nombre_liga(eq.get("liga"), eq.get("competicion")),
            "liga_slug": eq.get("liga"),
            "deporte": eq.get("deporte") or eq.get("sport"),
            "division": None,
        }

    ctx["encontrado"] = True
    ctx["id"] = info["id"]
    ctx["nombre"] = info["nombre"]
    ctx["competicion"] = info["competicion"]
    ctx["liga"] = info["liga_slug"]
    ctx["deporte"] = DEPORTE_ES.get(info["deporte"], info["deporte"])
    ctx["division"] = info.get("division")

    if info["liga_slug"] == "mlb" and info["id"]:
        ctx["plantilla"] = [p["nombre"] for p in mlb_roster(info["id"])]
        if fecha_iso:
            partidos = mlb_partidos_fecha(info["id"], fecha_iso)
            if partidos:
                juego = partidos[0]
                ctx["partido_real"] = juego
                es_local = _norm(juego.get("local") or "") == _norm(info["nombre"] or "")
                ctx["rival_real"] = juego.get("visitante") if es_local else juego.get("local")
                # Id del rival en MLB: hace falta para el H2H de la temporada.
                ctx["rival_id"] = (
                    juego.get("localId") if es_local else juego.get("visitanteId")
                ) or mlb_team_id(ctx["rival_real"])
                titulares = mlb_lanzadores_titulares(juego.get("gamePk"))
                if titulares:
                    ctx["notas"].append(
                        "Lanzadores titulares confirmados: "
                        + ", ".join(f"{t['nombre']} ({t['equipo']})" for t in titulares)
                    )
            else:
                ctx["notas"].append(
                    f"{info['nombre']} NO tiene partido en el calendario de MLB "
                    f"para el {fecha_iso}. No inventes un enfrentamiento para esa fecha."
                )
        return ctx

    sport_path = DEPORTE_PATH.get(info["deporte"], info["deporte"])
    if sport_path and info["liga_slug"] and info["id"]:
        ctx["plantilla"] = [p["nombre"] for p in espn_roster(
            sport_path, info["liga_slug"], info["id"])]
        ficha = espn_team_info(sport_path, info["liga_slug"], info["id"])
        if ficha.get("record"):
            ctx["record"] = ficha["record"]
        if ficha.get("tabla"):
            ctx["tabla"] = ficha["tabla"]
    return ctx

    if nombre_equipo:
        objetivo = _norm(nombre_equipo)
        real = _norm(res["equipo_real"] or "")
        coincide = bool(real) and (
            objetivo in real or real in objetivo
            or _puntuar(real, objetivo) >= 0.6
            or _puntuar(objetivo, real) >= 0.6
        )
        res["pertenece"] = coincide
        if not coincide:
            res["motivo"] = (
                f"ERROR DE PLANTILLA: {jugador['nombre']} juega en "
                f"{res['equipo_real']}, NO en {nombre_equipo}. "
                f"Competicion real: {res['competicion']}."
            )
    return res

    clave = f"mlb:box:{game_pk}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/game/{game_pk}/boxscore")
    titulares = []
    if data:
        for lado in ("away", "home"):
            equipo = ((data.get("teams") or {}).get(lado) or {})
            for _pid, info in (equipo.get("players") or {}).items():
                posicion = ((info.get("position") or {}).get("abbreviation") or "")
                bateo = ((info.get("stats") or {}).get("battingOrder") or "")
                # El lanzador titular es el que bate 1 en el orden de bateo
                if posicion == "P" and str(bateo) in ("1", "1.0"):
                    titulares.append({
                        "equipo": (equipo.get("team") or {}).get("name"),
                        "nombre": (info.get("person") or {}).get("fullName"),
                        "posicion": posicion,
                    })


# --------------------------------------------- extraccion de entidades del texto

_RE_NOMBRE_PROPIO = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ'’-]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ'’-]+){1,2})\b"
)
# El regex anterior es codicioso y se come varias palabras sueltas seguidas
# ("Beisbol Over Under" -> un unico "nombre"). Se parte el texto en palabras
# no permitidas antes de buscar, para que cada candidato sea contiguo.
_RE_PALABRA_PROHIBIDA = re.compile(
    r"\b(?:over|under|total|totales|handicap|partido|equipo|jugador|apuesta|"
    r"cuota|pick|edge|ganador|marcador|stats|estadisticas|deporte|mes|para|con|"
    r"por|que|dame|analiza|over/under|o/u)\b",
    re.IGNORECASE,
)
_RE_VS = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][\w'’-]*(?:\s+[A-ZÁÉÍÓÚÑ][\w'’-]+){0,3})\s+(?:vs\.?|v\.?|contra)\s+"
    r"([A-ZÁÉÍÓÚÑ][\w'’-]*(?:\s+[A-ZÁÉÍÓÚÑ][\w'’-]+){0,3})"
)

# Palabras que parecen nombre propio pero son vocabulario del analisis.
_NO_ES_JUGADOR = {
    "vs", "contra", "partido", "equipo", "equipos", "jugador", "jugadores", "stats",
    "estadisticas", "promedio", "ponches", "strikeout", "strikeouts", "hits", "runs",
    "apuesta", "apuestas", "cuota", "cuotas", "confianza", "pick", "picks", "edge",
    "total", "totales", "handicap", "ganador", "marcador", "deporte", "baseball",
    "beisbol", "futbol", "basquetbol", "tenis", "hockey", "over", "under", "mes",
    "local", "visitante", "si", "no", "que", "para", "con", "por", "dame", "analiza",
}


def _filtrar_nombres(candidatos, excluir=(), min_palabras=2):
    salida, vistos = [], set()
    excluir_n = {_norm(e) for e in excluir if e}
    for c in candidatos:
        c = (c or "").strip(" .,-()")
        if not c or len(c) < 3 or len(c.split()) < min_palabras:
            continue
        clave = _norm(c)
        if not clave or clave in _NO_ES_JUGADOR or clave in excluir_n:
            continue
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(c)
    return salida


def extraer_entidades(mensaje):
    """
    Saca del mensaje los JUGADORES y EQUIPOS citados.

    El formato habitual es "Equipo (Jugador) vs Equipo (Jugador)", asi que
    los parentesis se tratan como jugadores y el texto exterior como equipos.
    """
    mensaje = mensaje or ""
    jugadores, equipos = [], []

    for m in re.finditer(
        r"([A-ZÁÉÍÓÚÑ][\w'’-]*(?:\s+[A-ZÁÉÍÓÚÑ][\w'’-]+){0,3})\s*\(([^)]{3,60})\)", mensaje
    ):
        equipos.append(m.group(1))
        jugadores.extend(_filtrar_nombres([m.group(2)]))

    # El "vs" puede venir seguido de un parentesis con el jugador
    # ("Rangers (Mahle) vs Phillies (Sanchez)"), asi que se quitan los
    # parentesis antes de buscar los equipos: si no, el segundo equipo
    # ("vs Phillies (Sanchez)") nunca se captura.
    sin_parentesis = re.sub(r"\([^)]*\)", " ", mensaje)
    for a, b in _RE_VS.findall(sin_parentesis):
        equipos.extend([a, b])
    jugadores.extend(_filtrar_nombres(
        _RE_NOMBRE_PROPIO.findall(_RE_PALABRA_PROHIBIDA.sub(" | ", mensaje))
    ))

    return (
        # Un nombre que ya salio como equipo no puede ser un jugador
        # ("Philadelphia Phillies" se detectaba como si fuera un jugador).
        _filtrar_nombres(jugadores, excluir=equipos)[:5],
        # Los equipos pueden ser de una sola palabra ("Barcelona", "Inter",
        # "Celtics"), asi que se permiten desde 1 palabra.
        _filtrar_nombres(equipos, min_palabras=1)[:4],
    )


def construir_contexto_verificacion(mensaje, fecha_iso=None):
    """
    Texto compacto con la VERIFICACION REAL (plantilla + competicion) que se
    inyecta antes de pedir el analisis. Impide inventar partidos con jugadores
    que no pertenecen al equipo indicado.
    """
    if not mensaje or not mensaje.strip():
        return ""

    from datetime import datetime
    fecha_iso = fecha_iso or datetime.now().strftime("%Y-%m-%d")

    jugadores, equipos = extraer_entidades(mensaje)
    cuerpo = []
    errores_plantilla = []

    for i, nombre in enumerate(jugadores):
        # Contrasta el jugador contra el equipo que el usuario le asocio.
        equipo_asociado = None
        for idx, eq in enumerate(equipos):
            if len(jugadores) > idx and jugadores[idx] == nombre:
                equipo_asociado = eq
                break
        if equipo_asociado is None and equipos and len(jugadores) == len(equipos):
            pos = jugadores.index(nombre)
            if pos < len(equipos):
                equipo_asociado = equipos[pos]

        v = verificar_jugador_en_equipo(nombre, equipo_asociado)
        if not v.get("encontrado"):
            cuerpo.append(f"- {nombre}: {v['motivo']}")
            continue
        linea = (f"- {nombre}: el jugador real es {v['jugador']} y juega en "
                 f"{v['equipo_real'] or 'equipo no identificado'} "
                 f"({v['competicion']}).")
        if v.get("motivo"):
            linea += f"  AVISO: {v['motivo']}"
            if v.get("pertenece") is False:
                errores_plantilla.append(v["motivo"])
        cuerpo.append(linea)

    for nombre in equipos:
        ctx = contexto_equipo(nombre, fecha_iso)
        if not ctx.get("encontrado"):
            cuerpo.extend(f"- {n}" for n in ctx.get("notas", []))
            continue
        bloque = [f"EQUIPO NOMBRADO: {nombre}", f"  Nombre real: {ctx['nombre']}",
                  f"  Competicion: {ctx['competicion']}"]
        if ctx.get("division"):
            bloque.append(f"  Division: {ctx['division']}")
        if ctx.get("record"):
            bloque.append(f"  Record: {ctx['record']}")
        if ctx.get("tabla"):
            bloque.append(f"  Posicion en la tabla: {ctx['tabla']}")
        if ctx.get("partido_real"):
            pr = ctx["partido_real"]
            bloque.append(
                f"  PARTIDO REAL DEL {fecha_iso}: {pr['visitante']} vs {pr['local']} "
                f"({pr.get('estado') or 'programado'}). Rival real: {ctx['rival_real']}."
            )
        if ctx.get("plantilla"):
            bloque.append(
                f"  Plantilla activa ({len(ctx['plantilla'])} jugadores): "
                + ", ".join(ctx["plantilla"][:20])
            )
        bloque.extend(f"  * {n}" for n in ctx.get("notas", []))
        cuerpo.append("\n".join(bloque))

    if not cuerpo:
        return ""

    cabecera = (
        "\n\n" + "=" * 64 + "\n"
        "DATOS VERIFICADOS DE PLANTILLAS Y COMPETICION (FUENTE OFICIAL)\n"
        "Estos datos son la VERDAD del partido. Usalos como base:\n"
        "- Si un jugador NO aparece en la plantilla del equipo nombrado, esa\n"
        "  combinacion NO existe: no la analices como si fuera real.\n"
        "- Si el rival real de la fecha es otro, analiza ESE partido.\n"
        "- Elige mercados que existan en la competicion indicada.\n"
        + "=" * 64 + "\n"
    )

    if errores_plantilla:
        cabecera += (
            "\n*** ALERTA DE PLANTILLA CONFIRMADA ***\n"
            + "\n".join(f"- {e}" for e in errores_plantilla)
            + "\nEl partido que planteo el usuario NO EXISTE como tal. Debes:\n"
              "1) Decir con claridad que el jugador no pertenece a ese equipo.\n"
              "2) Analizar el partido REAL de la competicion indicada abajo.\n"
              "3) NO presentar el partido inventado como si fuera real.\n"
        )

    cierre = (
        "\n\nREGLA: si el jugador que nombro el usuario no esta en la plantilla\n"
        "del equipo, dilo con claridad, indica su equipo real y entrega el analisis\n"
        "del partido que SI existe en esa competicion."
    )
    return cabecera + "\n".join(cuerpo) + cierre



def mlb_carga_calendario(team_id, temporada):
    """
    Fatiga y calendario: cuantos partidos jugados en los ultimos 7 dias y
    cuantos les tocan en los siguientes. El agotamiento cambia el juego.
    """
    from datetime import datetime, timedelta
    hoy = datetime.now()
    clave = f"mlb:fatiga:{team_id}:{temporada}:{hoy.strftime('%Y-%m-%d')}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    jugados, proximos, detalle = 0, 0, []
    data = _get(f"{MLB_BASE}/schedule", params={
        "sportId": 1, "teamId": team_id, "season": temporada,
    })
    for dia in (data or {}).get("dates", []) or []:
        for juego in dia.get("games", []) or []:
            fecha = (juego.get("gameDate") or "")[:10]
            try:
                d = datetime.strptime(fecha, "%Y-%m-%d")
            except ValueError:
                continue
            dias = (d.date() - hoy.date()).days
            if -7 <= dias < 0:
                jugados += 1
            elif 0 <= dias <= 7:
                proximos += 1
                teams = juego.get("teams", {}) or {}
                rival = ((teams.get("away") or {}).get("team", {}) or {}).get("name")
                local = ((teams.get("home") or {}).get("team", {}) or {}).get("name")
                detalle.append(f"{fecha}: {rival} @ {local}")
    return _cache_put(clave, {
        "jugados_7d": jugados,
        "proximos_7d": proximos,
        "detalle": detalle[:5],
    })


def mlb_h2h_equipo(team_id, rival_id, temporada, limite=6):
    """Partidos directos entre dos equipos de MLB en la temporada."""
    clave = f"mlb:h2h:{team_id}:{rival_id}:{temporada}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado
    data = _get(f"{MLB_BASE}/schedule", params={
        "sportId": 1, "teamId": team_id, "opponentId": rival_id, "season": temporada,
    })
    juegos = []
    for dia in (data or {}).get("dates", []) or []:
        for juego in dia.get("games", []) or []:
            teams = juego.get("teams", {}) or {}
            away = (teams.get("away") or {}).get("team", {}) or {}
            home = (teams.get("home") or {}).get("team", {}) or {}
            juegos.append({
                "fecha": juego.get("officialDate"),
                "partido": f"{away.get('name')} @ {home.get('name')}",
                "marcador": (f"{(teams.get('away') or {}).get('score')}-"
                             f"{(teams.get('home') or {}).get('score')}"),
                "estado": ((juego.get("status") or {}).get("detailedState") or ""),
            })
    return _cache_put(clave, sorted(juegos, key=lambda j: j.get("fecha") or "")[-limite:])



# ------------------------------------------- investigacion profunda (noticias)

# Consultas pensadas para sacar MOTIVACION y contexto, no solo marcador.
_PISTAS_MLB = [
    "{equipo} playoff race {mes} {anio} standings clinch",
    "{equipo} injuries {mes} {anio}",
    "{equipo} {rival} preview {mes} {anio}",
    "{equipo} rotation {mes} {anio}",
]
_PISTAS_FUTBOL = [
    "{equipo} Champions League {mes} {anio} qualification",
    "{equipo} injuries lesionados {mes} {anio}",
    "{equipo} {rival} previa {mes} {anio}",
    "{equipo} forma ultimos partidos {mes} {anio}",
]
_PISTAS_GENERICAS = [
    "{equipo} injuries {mes} {anio}",
    "{equipo} {rival} preview {mes} {anio}",
    "{equipo} standings {mes} {anio}",
]

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _pistas(equipo, rival, competicion=""):
    comp = (competicion or "").lower()
    if "mlb" in comp or "beisbol" in comp:
        plantilla = _PISTAS_MLB
    elif any(k in comp for k in ("liga", "champions", "europa", "mls", "futbol")):
        plantilla = _PISTAS_FUTBOL
    else:
        plantilla = _PISTAS_GENERICAS

    from datetime import datetime
    mes, anio = _MESES[datetime.now().month - 1], datetime.now().year
    return [
        p.format(equipo=equipo, rival=rival or "rival", mes=mes, anio=anio)
        for p in plantilla
    ]


def investigar_noticias(equipo, rival=None, competicion="", max_resultados=6,
                       limite_pistas=1):
    """
    Noticias recientes del equipo: lesiones, motivacion, rotacion, previa.

    Usa el mismo buscador (ddgs) que ya usa 365AI, asi que no se anaden
    dependencias.

    OJO con el coste: una consulta de ddgs tarda ~10s. Por eso aqui solo se
    lanza UNA consulta de arranque y con un reloj de pared; el resto de la
    investigacion la hace la IA con su herramienta buscar_web, que ya sabe
    cuando merece la pena buscar mas. Asi el chat no se cuelga.

    Si no hay resultados, devuelve '' y el analisis sigue adelante con los
    datos estructurados (tabla, motivacion, H2H), que no dependen de la web.
    """
    if not equipo:
        return ""
    clave = f"noticias:{_norm(equipo)}:{_norm(rival)}:{competicion}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    try:
        from ddgs import DDGS
    except ImportError:
        return _cache_put(clave, "")

    presupuesto = float(os.getenv("AI36_NOTICIAS_SEGUNDOS", "10"))
    vistos, notas = set(), []

    def _barrer():
        """Corre las consultas y va llenando `notas` en el hilo."""
        try:
            with DDGS(timeout=presupuesto) as ddgs:
                for consulta in _pistas(equipo, rival, competicion)[:limite_pistas]:
                    try:
                        for r in ddgs.text(consulta, max_results=max_resultados):
                            titulo = (r.get("title") or "").strip()
                            cuerpo = (r.get("body") or "").strip()
                            if not cuerpo or titulo in vistos:
                                continue
                            vistos.add(titulo)
                            notas.append(f"- {titulo}: {cuerpo[:220]}")
                    except Exception:
                        continue
        except Exception:
            pass

    # La busqueda web puede colgarse varios segundos. Se lanza en un hilo
    # daemon y se espera como mucho `presupuesto`: si se pasa, se devuelve
    # lo que ya se reunio en vez de bloquear la peticion del chat.
    hilo = threading.Thread(target=_barrer, daemon=True)
    hilo.start()
    hilo.join(presupuesto)

    return _cache_put(clave, "\n".join(notas))



# ------------------------------------------- investigacion completa del partido

def _bloque_mlb_equipo(equipo, ctx, rival_nombre, temporada, rival_id=None):
    """Ficha profunda de un equipo de MLB: tabla, motivacion, ritmo, carga."""
    lineas = []
    tid = ctx.get("id")
    estado = stance_mlb(equipo, temporada)
    if estado:
        lineas.append(
            f"  Tabla: {estado.get('wins')}-{estado.get('losses')} "
            f"(pct {estado.get('pct')}), differential de carreras "
            f"{estado.get('runDiff')}, ultimos 10: {estado.get('lastTen')}, "
            f"casa {estado.get('home')}, fuera {estado.get('away')}."
        )
        motiv = motivation_text(estado)
        if motiv:
            lineas.append(f"  MOTIVACION: {motiv}")

    carga = mlb_carga_calendario(tid, temporada)
    if carga.get("jugados_7d") is not None:
        linea = (f"  Carga de calendario: {carga['jugados_7d']} partidos en los ultimos "
                 f"7 dias, {carga['proximos_7d']} en los siguientes.")
        if (carga["jugados_7d"] or 0) >= 6:
            linea += " Agotamiento alto: el rendimiento suele bajar."
        elif (carga["proximos_7d"] or 0) >= 7:
            linea += " Temporada avanzada y tramo denso al frente."
        lineas.append(linea)
        for d in carga.get("detalle", [])[:3]:
            lineas.append(f"    - {d}")

    if rival_id:
        h2h = mlb_h2h_equipo(tid, rival_id, temporada)
        # Solo los partidos YA DISPUTADOS: los programados vienen con
        # marcador "0-0" o None y ensuciarian el historico.
        jugados = [
            j for j in h2h
            if j.get("marcador") and j["marcador"] not in ("None-None", "0-0")
        ]
        if jugados:
            linea = "  H2H temporada: " + "; ".join(
                f"{j['fecha']} {j['partido']} {j['marcador']}" for j in jugados[-4:]
            )
            # Quien ganó la serie lleva ventaja psicológica de cara al partido.
            ganados = {}
            for j in jugados:
                try:
                    a, b = (int(x) for x in j["marcador"].split("-"))
                except (ValueError, TypeError):
                    continue
                visitante = (j.get("partido") or "").split(" @ ")[0]
                local = (j.get("partido") or "").split(" @ ")[-1]
                if a > b:
                    ganados[visitante] = ganados.get(visitante, 0) + 1
                elif b > a:
                    ganados[local] = ganados.get(local, 0) + 1
            if ganados:
                dominador = max(ganados, key=ganados.get)
                linea += (f"  Suma la serie {dominador} "
                          f"{ganados[dominador]}-{sum(ganados.values()) - ganados[dominador]}.")
            lineas.append(linea)
    return lineas


def construir_investigacion(mensaje, fecha_iso=None, con_noticias=True):
    """
    INVESTIGACION PROFUNDA del partido: que se juega, por quien, contra quien,
    en que competicion, con que motivacion, en que forma y con que-fatiga.

    Es el bloque que el modelo recibe antes de analizar. Devuelve "" si el
    mensaje no menciona entidades reconocibles.
    """
    if not mensaje or not mensaje.strip():
        return ""
    from datetime import datetime
    hoy = fecha_iso or datetime.now().strftime("%Y-%m-%d")
    temporada = int(hoy[:4])

    jugadores, equipos = extraer_entidades(mensaje)
    cuerpo = []

    # --- 1. Jugadores: equipo real, rol y si el partido es real ---
    for nombre in jugadores:
        v = verificar_jugador_en_equipo(nombre)
        if not v.get("encontrado"):
            cuerpo.append(f"- {nombre}: {v['motivo']}")
            continue
        linea = (f"- {nombre} ({v.get('posicion') or 'jugador'}) juega en "
                 f"{v['equipo_real']} dentro de {v['competicion']}.")
        if v.get("motivo"):
            linea += f"  {v['motivo']}"
        cuerpo.append(linea)

    # --- 2 y 3. Equipos: cabecera + profundidad de MLB (tabla, motivacion,
    #         carga de calendario, H2H y plantilla) en el MISMO bucle, para
    #         que cada dato quede bajo su equipo y no al final mezclado.
    fichas = []
    for nombre in equipos:
        ctx = contexto_equipo(nombre, hoy)
        if not ctx.get("encontrado"):
            cuerpo.extend(f"- {n}" for n in ctx.get("notas", []))
            continue
        ctx["nombre_pedido"] = nombre
        fichas.append(ctx)
        bloque = []
        bloque.append("")
        bloque.append(f"EQUIPO: {nombre} -> {ctx['nombre']}")
        bloque.append(f"  Competicion: {ctx['competicion']}"
                      + (f" | Division: {ctx['division']}" if ctx.get("division") else ""))
        if ctx.get("record"):
            bloque.append(f"  Record: {ctx['record']}")
        if ctx.get("tabla"):
            bloque.append(f"  En la tabla: {ctx['tabla']}")
        if ctx.get("partido_real"):
            pr = ctx["partido_real"]
            bloque.append(
                f"  PARTIDO REAL DE HOY ({hoy}): {pr['visitante']} vs {pr['local']} "
                f"({pr.get('estado') or 'programado'}). Rival real: {ctx['rival_real']}."
            )
        elif ctx.get("rival_real"):
            bloque.append(f"  Rival: {ctx['rival_real']}")

        if ctx.get("liga") == "mlb":
            bloque.extend(_bloque_mlb_equipo(
                ctx.get("nombre"), ctx, ctx.get("rival_real"),
                temporada, ctx.get("rival_id"),
            ))
            if ctx.get("plantilla"):
                bloque.append(
                    f"  Plantilla ({len(ctx['plantilla'])}): "
                    + ", ".join(ctx["plantilla"][:16])
                )
            for n in ctx.get("notas", []):
                if "titulares" in n.lower():
                    bloque.append(f"  {n}")

        cuerpo.extend(bloque)

    if not cuerpo:
        return ""

    # --- 4. Noticias: lesiones, rotacion y motivacion reciente ---
    if con_noticias and fichas:
        principal = fichas[0]
        noticias = investigar_noticias(
            principal.get("nombre"), principal.get("rival_real"),
            principal.get("competicion") or "",
        )
        if noticias:
            cuerpo.append("")
            cuerpo.append("NOTICIAS Y CONTEXTO RECIENTE (lesiones, rotacion, motivacion):")
            cuerpo.append(noticias)

    return (
        "\n\n" + "#" * 66 + "\n"
        "INVESTIGACION PROFUNDA DEL PARTIDO (datos oficiales + noticias)\n"
        "Responde a: QUE se juega, en QUE competicion, POR que importa\n"
        "(motivacion), en QUE forma llega cada equipo y con que desgaste.\n"
        "Usa TODO esto como base. No inventes lo que no este aqui.\n"
        + "#" * 66 + "\n"
        + "\n".join(cuerpo)
    )

    return _cache_put(clave, titulares)
