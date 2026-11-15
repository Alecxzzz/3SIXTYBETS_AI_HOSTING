# -*- coding: utf-8 -*-
"""
Historico real de partidos de futbol via la API JSON de FotMob.

Por que esta y no footystats/sofascore: probadas las tres, footystats
devuelve una shell de 14 bytes (los datos se piden por AJAX), la API de
sofascore responde 403 y ATP 403 por Cloudflare. La de fotmob responde 200
con JSON limpio en /api/data/.

Sirve para lo que pedia el usuario: medir el % real de un mercado sobre los
ULTIMOS PARTIDOS (ej: quantas veces marco el equipo 2+ goles en los ultimos
10), en vez de fiarse de la categoria del mercado.
"""
import json
import threading
import unicodedata
import time
from datetime import date, datetime, timedelta

import requests

BASE = "https://www.fotmob.com/api/data/"
H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.fotmob.com/",
}
_cache = {}
_lock = threading.Lock()
TTL = 1800  # 30 min: el historico no cambia cada minuto


def _get(ruta, params=None, ttl=None):
    clave = ruta + "?" + json.dumps(params or {}, sort_keys=True)
    ahora = time.time()
    # Los datos en vivo (matchDetails) cambian cada jugada: esas llamadas pasan
    # ttl corto. El historico sigue usando el TTL largo de arriba.
    vida = TTL if ttl is None else ttl
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < vida:
            return hit[1]
    try:
        r = requests.get(f"{BASE}{ruta}", params=params, headers=H, timeout=20)
        # r.json() falla con UnicodeDecodeError en algunas respuestas UTF-8
        # con caracteres de equipos, asi que se decodifica a mano desde bytes.
        datos = json.loads(r.content.decode("utf-8", "replace"))
    except Exception:
        datos = None
    with _lock:
        _cache[clave] = (ahora, datos)
    return datos


def _repara(t):
    """FotMob sirve los nombres con doble UTF-8: 'Bayern MÃ¼nchen'.

    Se repara pasando los bytes latin-1 por utf-8. Sin esto 'Bayern Munich' no
    encuentra 'Bayern München' y el equipo se pierde.
    """
    if not t:
        return ""
    try:
        return t.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t


def _clave(t):
    """Clave de comparacion sin acentos ni simbolos: 'Bayern München' -> 'bayernmunchen'."""
    t = _repara(t or "")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if c.isalnum()).lower()

_PALABRAS_VACIAS = ("de", "del", "la", "las", "los", "el", "the", "of", "and", "y")


def _clave_equipo(t):
    """Clave de EQUIPO, como _clave pero sin conectores.

    'Estudiantes de Buenos Aires' == 'Estudiantes Buenos Aires'. NO se quitan
    palabras que identifican al club: 'United' no es un conector, es parte del
    nombre (quitarlo fue justo lo que confundio a Atlanta con Atlanta United).
    """
    t = _repara(t or "").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c if (c.isalnum() or c.isspace()) else " " for c in t)
    return "".join(p for p in t.split() if p not in _PALABRAS_VACIAS)




def _empareja(a, b):
    """True si dos claves de equipo son el MISMO club.

    Solo se admite contencion cuando el nombre corto tiene cuerpo suficiente y
    lo que sobra es minimo ('realmadridcf' vs 'realmadrid' -> si; 'atlanta' vs
    'atlantaunited' -> NO, son clubes distintos).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    if len(corto) < 5:
        return False
    if corto in largo:
        return (len(largo) - len(corto)) <= 3
    return False


def mismo_partido(local_a, visita_a, local_b, visita_b):
    """True si los dos pares de nombres describen el mismo partido (en cualquier orden)."""
    a, b = _clave_equipo(local_a), _clave_equipo(visita_a)
    c, d = _clave_equipo(local_b), _clave_equipo(visita_b)
    return (_empareja(a, c) and _empareja(b, d)) or (_empareja(a, d) and _empareja(b, c))


_GENERICOS = {
    "club", "atletico", "atletica", "deportivo", "deportiva", "fc", "cf", "sc",
    "afc", "cd", "ac", "ca", "the", "team", "sporting", "racing",
}


def _palabras_distintivas(t):
    """Palabras que identifican al club, sin genericos: 'Club Atlético
    Estudiantes' -> {'estudiantes'}. Sirve para unir el nombre de ESPN
    ('Estudiantes (Buenos Aires)') con el de FotMob ('Club Atlético Estudiantes')."""
    t = _repara(t or "").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c if (c.isalnum() or c.isspace()) else " " for c in t)
    fuera = set(_PALABRAS_VACIAS) | _GENERICOS
    return {p for p in t.split() if p not in fuera and len(p) >= 4}


def _mismo_equipo(a, b):
    """Como _empareja pero tolera que cada fuente nombre al club distinto.

    Solo se usa cuando ADEMAS el otro equipo del partido coincide y la fecha
    esta dentro de la ventana: dos 'Estudiantes' distintos jugando contra el
    mismo rival el mismo dia es practicamente imposible.
    """
    if _empareja(_clave_equipo(a), _clave_equipo(b)):
        return True
    return bool(_palabras_distintivas(a) & _palabras_distintivas(b))



def buscar_equipo(nombre):
    """'Arsenal' -> {'id': 9825, ...}. Devuelve None si no lo encuentra."""
    if not nombre:
        return None
    datos = _get("search/suggest", {"term": nombre})
    if not isinstance(datos, list):
        return None
    objetivo = _clave(nombre)
    candidatos = []
    for bloque in datos:
        for s in (bloque or {}).get("suggestions") or []:
            if s.get("type") in ("team", "country") and s.get("id"):
                candidatos.append(s)
    for s in candidatos:
        if _clave(s.get("name")) == objetivo:
            return s
    for s in candidatos:
        nombre_s = _clave(s.get("name"))
        if objetivo and (objetivo in nombre_s or nombre_s in objetivo):
            return s
    return candidatos[0] if candidatos else None


def _partidos_de_fecha(dia):
    datos = _get("matches", {"date": dia.strftime("%Y%m%d")})
    out = []
    for liga in (datos or {}).get("leagues") or []:
        for p in liga.get("matches") or []:
            # VERIFICADO contra la API: los partidos terminados traen
            # statusId 5 o 6 (6 es lo normal). El 2 que se puso al principio
            # no existe y devolvia 0 partidos en silencio.
            if str(p.get("statusId")) not in ("5", "6", "7"):
                continue
            h, a = p.get("home") or {}, p.get("away") or {}
            try:
                gf, ga = int(h.get("score")), int(a.get("score"))
            except (TypeError, ValueError):
                continue
            out.append({
                "id": p.get("id"),
                "date": dia.isoformat(),
                "home_id": h.get("id"), "away_id": a.get("id"),
                "home": h.get("name"), "away": a.get("name"),
                "gf": gf, "ga": ga,
            })
    return out


def ultimos_partidos(equipo_id, n: int = 10, dias_atras: int = 45):
    """Los ultimos N partidos TERMINADOS de un equipo, del mas reciente al mas viejo.

    Se recorre hacia atras por fecha porque /api/data/matches solo da un dia.
    Con 45 dias de margen hay de sobra para 10 partidos en ligas con partidos dos veces por semana.
    """
    equipo_id = int(equipo_id)
    out = []
    hoy = date.today()
    for offset in range(0, dias_atras):
        dia = hoy - timedelta(days=offset)
        for p in _partidos_de_fecha(dia):
            if p["home_id"] == equipo_id or p["away_id"] == equipo_id:
                p["local"] = (p["home_id"] == equipo_id)
                p["equipo_gol"] = p["gf"] if p["local"] else p["ga"]
                p["rival_gol"] = p["ga"] if p["local"] else p["gf"]
                out.append(p)
                if len(out) >= n:
                    return out[:n]
    return out


def prob_equipo_equipo(equipo_id, linea, lado="over", n: int = 10):
    """% real de los ultimos N partidos en los que el equipo marco >= linea."""
    if linea is None:
        return None
    partidos = ultimos_partidos(equipo_id, n)
    if len(partidos) < min(5, n):
        return None  # muestra insuficiente: no se afirma nada
    if lado == "over":
        aciertos = sum(1 for p in partidos if p["equipo_gol"] >= linea)
    else:
        aciertos = sum(1 for p in partidos if p["equipo_gol"] < linea)
    return int(round(aciertos * 100 / len(partidos)))


def prob_partido_ambos_marcan(equipo_id, n: int = 10):
    """BTTS: % de partidos donde marcaron ambos equipos."""
    partidos = ultimos_partidos(equipo_id, n)
    if len(partidos) < min(5, n):
        return None
    b = sum(1 for p in partidos if p["gf"] > 0 and p["ga"] > 0)
    return int(round(b * 100 / len(partidos)))





# ---------------------------------------------------------------------------
# ALINEACIONES (oficial y ONCE PROBABLE) con foto de cada jugador
# ---------------------------------------------------------------------------
# FotMob expone en matchDetails -> content.lineup los dos equipos con
# formation, rating, starters[], subs[] y unavailable[]; cada jugador trae
# id, dorsal, posicion, NOTA y las coordenadas (horizontalLayout.x/y en 0..1)
# que usa su propio campo. La foto va por CDN a partir del id:
#   https://images.fotmob.com/image_resources/playerimages/{id}.png

CDN_FOTO = "https://images.fotmob.com/image_resources/playerimages/{id}.png"

# Nombres de cantera/filiales que no deben ganar la busqueda del equipo
_RUIDO_EQUIPO = ("castilla", "u19", "u21", "u23", "(w)", " ii", " b", "reserves")


def foto_jugador(pid):
    return CDN_FOTO.format(id=pid) if pid else None


def equipo_id(nombre):
    """Nombre de equipo -> id de FotMob (cache 30 dias)."""
    if not nombre:
        return None
    datos = _get("search/suggest", {"term": nombre}, ttl=30 * 24 * 3600)
    if not isinstance(datos, list):
        return None
    for grupo in datos:
        for s in grupo.get("suggestions") or []:
            if s.get("type") != "team" or not s.get("id"):
                continue
            nombre_fm = (s.get("name") or "").lower()
            if any(x in nombre_fm for x in _RUIDO_EQUIPO):
                continue
            return int(s["id"])
    return None


def _partidos_equipo(tid, ttl=900):
    """Lista de partidos (pasados y futuros) de un equipo en FotMob."""
    datos = _get("teams", {"id": tid}, ttl=ttl)
    fixtures = ((datos or {}).get("fixtures") or {}).get("allFixtures") or {}
    return fixtures.get("fixtures") or []




def _partidos_del_dia(dia):
    """TODOS los partidos de FotMob de un dia, en cualquier estado.

    `_partidos_de_fecha` solo devuelve terminados (5/6/7). Un partido en juego
    (statusId 3) no existia para el resolver, y por eso la alineacion de un
    partido EN VIVO no se encontraba nunca.
    """
    datos = _get("matches", {"date": dia.strftime("%Y%m%d")}, ttl=60)
    salida = []
    for liga in (datos or {}).get("leagues") or []:
        for p in liga.get("matches") or []:
            h = p.get("home") or {}
            a = p.get("away") or {}
            if not p.get("id") or not h.get("name") or not a.get("name"):
                continue
            salida.append({
                "id": p.get("id"),
                "home": h.get("name"),
                "away": a.get("name"),
                "utc": (p.get("status") or {}).get("utcTime"),
                "estado": p.get("statusId"),
                "liga": liga.get("name"),
            })
    return salida


def buscar_partido(local, visita, fecha=None, ventana_h: int = 48):
    """matchId de FotMob para local vs visita (nombre normalizado + fecha).

    BUG REAL QUE ESTO CORRIGE: con ("Atlanta", "Estudiantes (Buenos Aires)") la
    version anterior devolvia el partido "Red Bull New York vs Atlanta United"
    (otro club, a 6 dias de distancia) porque:
      1) aceptaba "parecido" con que UN solo nombre estuviera contenido en el
         rival, y comparaba longitudes siempre contra el local;
      2) la diferencia de fecha solo ORDENABA, nunca filtraba.
    Resultado: el panel mostraba un XI oficial de un partido ajeno.

    Ahora:
      - los DOS equipos deben aparecer, uno por cada lado del partido;
      - la tolerancia de nombre es minima (ver _empareja);
      - si hay fecha, el partido debe caer dentro de `ventana_h` horas.
    Si no se cumple, None: mejor sin alineacion que con la de otro partido.

    Estrategia: PRIMERO la lista diaria completa de FotMob (incluye partidos EN
    VIVO, que _partidos_de_fecha descarta) emparejando los dos equipos; si eso
    no da nada, se cae al escaneo de calendarios por equipo.
    """
    if not local or not visita:
        return None

    ol, ov = _clave_equipo(local), _clave_equipo(visita)
    if not ol or not ov or ol == ov:
        return None

    cuando = _ts(fecha)
    candidatos = []

    # ---- 1) POR FECHA (via fiable) --------------------------------------
    # La lista diaria trae TODOS los estados, incluido el partido EN VIVO
    # (statusId 3) que _partidos_de_fecha descarta. Sin esto, un partido en
    # juego nunca se encontraba y la alineacion oficial no aparecia.
    # Ademas no depende de que search/suggest devuelva el club correcto
    # (habia dos "Atlanta": el argentino y Atlanta United de la MLS).
    if cuando:
        base = datetime.fromtimestamp(cuando)
        dias = [(base.date(), 0), (base.date() - timedelta(days=1), 1),
                (base.date() + timedelta(days=1), 1)]
        for dia, penal_dia in dias:
            for f in _partidos_del_dia(dia):
                directo = _mismo_equipo(local, f["home"]) and _mismo_equipo(visita, f["away"])
                cruzado = _mismo_equipo(local, f["away"]) and _mismo_equipo(visita, f["home"])
                if not (directo or cruzado):
                    continue
                ts = _ts(f.get("utc"))
                if ts and abs(ts - cuando) > ventana_h * 3600:
                    continue
                exacto = (
                    _empareja(ol, _clave_equipo(f["home"]))
                    and _empareja(ov, _clave_equipo(f["away"]))
                ) or (
                    _empareja(ol, _clave_equipo(f["away"]))
                    and _empareja(ov, _clave_equipo(f["home"]))
                )
                dif = abs(ts - cuando) if ts else 0
                candidatos.append((0 if exacto else 1, penal_dia, dif, f["id"]))

    # ---- 2) POR EQUIPO (respaldo, sin fecha o si no aparecio) -----------
    if not candidatos:
        for nombre in (local, visita):
            tid = equipo_id(nombre)
            if not tid:
                continue
            for f in _partidos_equipo(tid):
                h = _clave_equipo(((f.get("home") or {}).get("name")) or "")
                a = _clave_equipo(((f.get("away") or {}).get("name")) or "")
                if not h or not a or not f.get("id"):
                    continue

                directo = _empareja(ol, h) and _empareja(ov, a)
                cruzado = _empareja(ol, a) and _empareja(ov, h)
                if not (directo or cruzado):
                    continue

                ts = _ts((f.get("status") or {}).get("utcTime"))
                if cuando and ts and abs(ts - cuando) > ventana_h * 3600:
                    continue
                dif = abs(ts - cuando) if (ts and cuando) else 0
                candidatos.append((0 if directo else 1, 1, dif, f.get("id")))

    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (c[0], c[1], c[2]))
    return candidatos[0][3]


_ROLES_USUAL = {0: "Portero", 1: "Defensa", 2: "Medio", 3: "Delantero"}


def _rol(position_id, usual_id):
    """Portero/Defensa/Medio/Delantero a partir de lo que manda FotMob.

    Los suplentes traen `usualPlayingPositionId` (0..3, verificado: Merino=2
    medio, portero=0) y los titulares `positionId` (11 = portero, 32-38 defensa,
    63-77 medio, 85+ delantero). Si no encaja, se deja vacio: mejor sin dato
    que con uno inventado.
    """
    try:
        usual = int(usual_id)
    except (TypeError, ValueError):
        usual = None
    if usual in _ROLES_USUAL:
        return _ROLES_USUAL[usual]
    try:
        pos = int(position_id)
    except (TypeError, ValueError):
        return ""
    if pos == 11:
        return "Portero"
    if 30 <= pos <= 45:
        return "Defensa"
    if 60 <= pos <= 80:
        return "Medio"
    if 80 <= pos <= 120:
        return "Delantero"
    return ""


def _jugador(p):
    """Normaliza un jugador de FotMob (titular o suplente)."""
    rend = p.get("performance") or {}
    h = p.get("horizontalLayout") or {}
    return {
        "id": p.get("id"),
        "nombre": _repara(p.get("name") or ""),
        "dorsal": p.get("shirtNumber") or "",
        "pos_id": p.get("positionId"),
        "rol": _rol(p.get("positionId"), p.get("usualPlayingPositionId")),
        "x": round(float(h.get("x") or 0), 4),
        "y": round(float(h.get("y") or 0), 4),
        "nota": rend.get("rating"),
        "edad": p.get("age"),
        "pais": _repara(p.get("countryName") or ""),
        "pais_codigo": p.get("countryCode") or "",
        "valor": p.get("marketValue"),
        "foto": foto_jugador(p.get("id")),
    }


def _lado(equipo, lado):
    equipo = equipo or {}
    titulares = [_jugador(p) for p in equipo.get("starters") or []]
    suplentes = [_jugador(p) for p in equipo.get("subs") or []]
    bajas = []
    for p in equipo.get("unavailable") or []:
        info = p.get("injuryInformation") or {}
        # FotMob manda el detalle real en `unavailability`
        # (type + expectedReturn + expectedReturnDate).
        baja = p.get("unavailability") or {}
        tipo = baja.get("type") or info.get("type") or ""
        bajas.append({
            "nombre": _repara(p.get("name") or ""),
            "foto": foto_jugador(p.get("id")),
            "motivo": tipo,
            "tipo": tipo,
            "regreso": baja.get("expectedReturn") or info.get("expectedReturn") or "",
            "regreso_fecha": baja.get("expectedReturnDate") or "",
            "rol": _rol(p.get("positionId"), p.get("usualPlayingPositionId")),
        })
    return {
        "lado": lado,
        "equipo": _repara(equipo.get("name") or ""),
        "equipo_id": equipo.get("id"),
        "formacion": equipo.get("formation") or "",
        "nota": equipo.get("rating"),
        "entrenador": _repara(((equipo.get("coach") or {}).get("name")) or ""),
        "titulares": titulares,
        "suplentes": suplentes,
        "bajas": bajas,
    }

def _ts(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None



def _eventos_partido(mf):
    """Goles, asistencias y tarjetas del partido (para pintarlos en el campo)."""
    salida = []
    for e in ((mf or {}).get("events") or {}).get("events") or []:
        jug = e.get("player") or {}
        tipo = e.get("type") or ""
        if not tipo or not (jug.get("id") or e.get("assistPlayerId")):
            continue
        salida.append({
            "tipo": tipo,                                   # Goal / Card / Substitution...
            "minuto": e.get("timeStr") or e.get("time"),
            "jugador_id": jug.get("id"),
            "jugador": _repara(jug.get("name") or ""),
            "asistente_id": e.get("assistPlayerId"),
            "asistente": _repara(e.get("assistStr") or ""),
            "es_local": bool(e.get("isHome")),
            "propio": bool(e.get("ownGoal")),
        })
    return salida


def _disparos(shotmap):
    """Remates del jugador en el partido, normalizados (para el shot map)."""
    salida = []
    for s in shotmap or []:
        og = s.get("onGoalShot") or {}
        salida.append({
            "min": s.get("min"),
            "x": s.get("x"),
            "y": s.get("y"),
            "x_porteria": og.get("x"),
            "y_porteria": og.get("y"),
            "xg": _redondea(s.get("expectedGoals")),
            "xgot": _redondea(s.get("expectedGoalsOnTarget")),
            "tipo": s.get("shotType") or "",
            "situacion": s.get("situation") or "",
            "resultado": s.get("eventType") or "",
            "a_puerta": bool(s.get("isOnTarget")),
            "dentro_area": bool(s.get("isFromInsideBox")),
            "propio": bool(s.get("isOwnGoal")),
        })
    return salida


def _stats_jugadores(player_stats, ids):
    """Stats del partido por jugador.

    Forma real de FotMob (verificada):
      playerStats["<id>"] = { name, shirtNumber, isGoalkeeper,
                              stats: [ {title, key, stats: { "<etiqueta>": {stat:{value,total}} }} ] }
    El portero puede traer stats vacias.
    """
    salida = {}
    ps = player_stats or {}
    for pid in ids:
        p = ps.get(str(pid)) or ps.get(pid)
        if not isinstance(p, dict):
            continue
        grupos = []
        for g in p.get("stats") or []:
            if not isinstance(g, dict):
                continue
            crudo = g.get("stats")
            filas = []
            if isinstance(crudo, dict):
                for etiqueta, v in crudo.items():
                    st = (v or {}).get("stat") or {}
                    if st.get("value") is None:
                        continue
                    filas.append({
                        "etiqueta": _repara(etiqueta),
                        "valor": st.get("value"),
                        "total": st.get("total"),
                    })
            elif isinstance(crudo, list):
                for v in crudo:
                    if not isinstance(v, dict):
                        continue
                    st = v.get("stat") or {}
                    if st.get("value") is None:
                        continue
                    filas.append({
                        "etiqueta": _repara(v.get("title") or ""),
                        "valor": st.get("value"),
                        "total": st.get("total"),
                    })
            if filas:
                grupos.append({"grupo": _repara(g.get("title") or ""), "filas": filas[:12]})
        salida[str(pid)] = {
            "nombre": _repara(p.get("name") or ""),
            "dorsal": p.get("shirtNumber"),
            "portero": bool(p.get("isGoalkeeper")),
            "equipo": _repara(p.get("teamName") or ""),
            "disparos": _disparos(p.get("shotmap")),
            "grupos": grupos[:4],
        }
    return salida


def alineacion(match_id):
    """Alineacion OFICIAL de un partido de FotMob (o None si aun no hay).

    Incluye, del mismo matchDetails cacheado: goles/asistencias del partido
    (`eventos`) y las stats de cada jugador (`stats_jugador`).
    """
    datos = _get("matchDetails", {"matchId": match_id}, ttl=120)
    contenido = (datos or {}).get("content") or {}
    lin = contenido.get("lineup") or {}
    if not lin.get("homeTeam") or not lin.get("awayTeam"):
        return None

    local = _lado(lin.get("homeTeam"), "home")
    visita = _lado(lin.get("awayTeam"), "away")
    if not local["titulares"] or not visita["titulares"]:
        return None

    ids = [
        j.get("id")
        for j in (local["titulares"] + visita["titulares"]
                  + local["suplentes"] + visita["suplentes"])
        if j.get("id")
    ]
    mf = contenido.get("matchFacts") or {}

    return {
        "tipo": "oficial",
        "local": local,
        "visita": visita,
        "eventos": _eventos_partido(mf),
        "stats_jugador": _stats_jugadores(contenido.get("playerStats") or {}, ids),
    }


def tracker(match_id):
    """Tracker en vivo del partido: URL del widget `live_action` de Opta.

    `matchDetails` trae, dentro de `content.superlive`:

        {"showSuperLive": true,
         "superLiveUrl": "https://pub.fotmob.com/prod/news/api/law?..."}

    Esa URL no es una API JSON: es una pagina HTML de ~2 KB que monta el
    widget de Opta (`secure.widget.cloud.opta.net`), el mismo tracker animado
    con la pelota, la direccion de juego y las ultimas acciones que se ve en
    la web de FotMob. Se comprobo que responde 200 sin `X-Frame-Options` ni
    `Content-Security-Policy`, asi que se puede embeber en un <iframe>.

    No hay token que caduque: los ids (`matchid`, `competition`, `season`,
    `srmatchid`) viajan en la propia URL y se renuevan con cada matchDetails.

    Devuelve None si el partido no trae `superLiveUrl` (FotMob no publica el
    widget en todas las competiciones). Ojo: el campo `showSuperLive` NO sirve
    como filtro -- en partidos ya terminados no viene y `superLiveUrl` sigue
    siendo valido (el widget muestra el resumen).
    """
    datos = _get("matchDetails", {"matchId": match_id}, ttl=120) or {}
    contenido = datos.get("content") or {}
    super_live = contenido.get("superlive") or {}
    url = str(super_live.get("superLiveUrl") or "").strip()
    if not url.startswith("https://"):
        return None
    estado = (datos.get("header") or {}).get("status") or {}
    return {
        "url": url,
        "en_vivo": bool(estado.get("ongoing")),
        "terminado": bool(estado.get("finished")),
    }


def _ultimos_finalizados(tid, n=6):
    """Ultimos partidos terminados de un equipo (mas reciente primero)."""
    finalizados = []
    for f in _partidos_equipo(tid):
        estado = f.get("status") or {}
        if not (estado.get("finished") or estado.get("scoreStr")):
            continue
        finalizados.append((_ts(estado.get("utcTime")) or 0, f))
    finalizados.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in finalizados[:n]]


def once_probable(nombre_equipo, n=6, lado="home"):
    """ONCE PROBABLE: titulares mas repetidos en los ultimos N partidos.

    Devuelve None si no hay datos suficientes. Se marca tipo="probable" para
    que la web lo etiquete como tal (nunca se hace pasar por oficial).
    """
    tid = equipo_id(nombre_equipo)
    if not tid:
        return None

    from collections import Counter

    apariciones = Counter()
    datos_jugador = {}
    formaciones = Counter()
    partidos = 0

    for f in _ultimos_finalizados(tid, n):
        lin = alineacion(f.get("id"))
        if not lin:
            continue
        equipo = lin["local"] if str(lin["local"].get("equipo_id")) == str(tid) else lin["visita"]
        if not equipo.get("titulares"):
            continue
        partidos += 1
        if equipo.get("formacion"):
            formaciones[equipo["formacion"]] += 1
        for j in equipo["titulares"]:
            clave = j.get("id") or j.get("nombre")
            apariciones[clave] += 1
            datos_jugador[clave] = j

    if partidos < 2 or len(apariciones) < 11:
        return None

    elegidos = []
    for clave, veces in apariciones.most_common():
        j = dict(datos_jugador[clave])
        j["apariciones"] = veces
        j["de"] = partidos
        j["posibilidad"] = int(round(veces * 100 / partidos))
        elegidos.append(j)
        if len(elegidos) == 11:
            break

    elegidos.sort(key=lambda j: (j.get("pos_id") or 99))
    return {
        "lado": lado,
        "equipo": nombre_equipo,
        "equipo_id": tid,
        "formacion": formaciones.most_common(1)[0][0] if formaciones else "",
        "nota": None,
        "entrenador": "",
        "titulares": elegidos,
        "suplentes": [],
        "bajas": [],
        "partidos": partidos,
    }



# ---------------------------------------------------------------------------
# EXTRAS DEL PARTIDO: arbitro, insights, goleadores y forma reciente
# ---------------------------------------------------------------------------
# Sale del mismo matchDetails que la alineacion (cache compartida), asi que
# pedirlo no cuesta una llamada extra.

_ETIQUETAS_STAT = {
    "playerRating": "Nota",
    "goals": "Goles",
    "expectedGoals": "xG",
    "goalAssist": "Asistencias",
    "minsPlayedGoal": "Min/gol",
    "ontargetScoringAtt": "Tiros a puerta",
    "motm": "MVP",
    "gamesPlayed": "Partidos",
    "minsPlayed": "Minutos",
}

# Cabeceras que FotMob manda en ingles para la comparativa de goleadores.
_SECCIONES_ES = {
    "Top scorers": "Máximos goleadores",
    "Top scorer": "Máximo goleador",
    "Most assists": "Máximos asistentes",
}

# FotMob manda los insights en ingles (su web los traduce en el navegador).
# Aqui van las plantillas al español; las que no esten se muestran tal cual.
_PLANTILLAS_ES = {
    "insights_goals_team": "Ha marcado {0} goles en sus últimos {1} partidos",
    "insights_goalless_team": "No ha marcado en sus últimos {0} partidos",
    "insights_not_won_team": "No gana desde hace {0} partidos",
    "insights_undefeated_team": "Lleva {0} partidos sin perder",
    "insights_not_clean_sheet": "No deja la portería a cero desde hace {0} partidos",
    "insights_clean_sheets": "Es el equipo con más porterías a cero de la competición ({0})",
    "insights_best_away": "Es el {0}.º mejor visitante de la temporada",
    "insights_average_goals_per_match": "Promedia {0} goles por partido",
    "insights_penalties_awarded": "Es el equipo al que más penaltis le pitan esta temporada ({0})",
    "insights_penalties_conceded": "Es el equipo que más penaltis concede esta temporada ({0})",
    "h2h_undefeated": "{0} no pierde con {1} en sus últimos {2} duelos ({3}V, {4}E)",
    "h2h_winstreak": "{0} ha ganado los últimos {2} partidos con {1}",
    "h2h_no_draws_v2": "{0} y {1} no han empatado ninguno de sus últimos {2} duelos",
    "insights_player_most_shots_on_target_for_team": "{0} es el que más tiros a puerta genera de {1} ({2})",
    "insights_player_most_chances_for_team": "{0} es el que más ocasiones claras crea en {1} ({2})",
    "insights_player_chances_created_rank_in_competition": "{0} es el {1}.º de la competición en ocasiones claras creadas ({2})",
    "insights_player_saves_per_match_rank_in_competition": "{0} es el {1}.º de la competición en paradas por partido ({2})",
}


def _formatea_insight(item, local="", visita=""):
    """Texto del insight en español (o el original si no hay plantilla)."""
    crudo = _repara(item.get("text") or "")
    plantilla = _PLANTILLAS_ES.get(item.get("localizedTextId") or "")
    if not plantilla:
        return crudo

    valores = []
    for v in item.get("statValues") or []:
        if isinstance(v, dict):
            # Los equipos y jugadores llegan como id + name: se usa el nombre
            nombre = v.get("name")
            valores.append(nombre if nombre else v.get("value"))
            continue
        texto = str(v)
        if texto in ("home_team", "away_team"):
            texto = local if texto == "home_team" else visita
        valores.append(texto)

    try:
        return plantilla.format(*valores)
    except (IndexError, KeyError):
        return crudo


def _texto_info(valor):
    """`infoBox` trae a veces dict ({name/value}) y a veces str/int."""
    if valor is None:
        return ""
    if isinstance(valor, dict):
        return _repara(valor.get("name") or valor.get("value") or "")
    return _repara(str(valor))


def _redondea(valor):
    """Deja los decimales a 2 cifras (xG llega como 3.2708001136779785)."""
    if isinstance(valor, float) and not valor.is_integer():
        return round(valor, 2)
    return valor


def extras_partido(match_id, local="", visita=""):
    """Arbitro, insights, comparativa de goleadores y forma reciente."""
    datos = _get("matchDetails", {"matchId": match_id}, ttl=180)
    contenido = (datos or {}).get("content") or {}
    mf = contenido.get("matchFacts") or {}
    if not mf:
        return None

    info = mf.get("infoBox") or {}

    arbitro = None
    ref = info.get("Referee") or {}
    if ref.get("text"):
        stats = {}
        for s in ref.get("stats") or []:
            tipo = s.get("type")
            if tipo in ("matches", "yellowCards", "redCards", "fouls", "penalties"):
                stats[tipo] = {
                    "valor": _redondea(s.get("value")),
                    "media": _redondea(s.get("average")),
                    "vs": s.get("averageType") or "",
                    "pct": _redondea(s.get("fillPercentage")),
                }
        arbitro = {
            "nombre": _repara(ref.get("text") or ""),
            "pais": _repara(ref.get("country") or ""),
            "bandera": ref.get("imgUrl"),
            "liga": ref.get("leagueName") or "",
            "stats": stats,
        }

    insights = []
    for i in mf.get("insights") or []:
        texto = _formatea_insight(i, local, visita)
        if texto:
            insights.append({"texto": texto, "color": i.get("color") or "#4ade80"})

    goleadores = None
    ts = mf.get("topScorers") or {}
    if ts.get("homePlayer") and ts.get("awayPlayer"):
        orden = ts.get("statsOrder") or []

        def _goleador(p):
            st = p.get("stats") or {}
            filas = [
                {"etiqueta": _ETIQUETAS_STAT.get(clave, clave), "valor": _redondea(st[clave])}
                for clave in orden
                if clave in st and st[clave] is not None
            ]
            return {
                "nombre": _repara(p.get("lastName") or p.get("fullName") or ""),
                "completo": _repara(p.get("fullName") or ""),
                "foto": foto_jugador(p.get("playerId")),
                "filas": filas[:6],
            }

        seccion = ts.get("section") or ""
        goleadores = {
            "titulo": _SECCIONES_ES.get(seccion, seccion or "Goleadores"),
            "local": _goleador(ts["homePlayer"]),
            "visita": _goleador(ts["awayPlayer"]),
        }

    forma = []
    for lado in (mf.get("teamForm") or [])[:2]:
        forma.append([
            {
                "resultado": f.get("resultString") or "",
                "rival": f.get("imageUrl"),
                "fecha": (f.get("date") or {}).get("utcTime"),
            }
            for f in (lado or [])[:5]
        ])

    return {
        "arbitro": arbitro,
        "insights": insights[:6],
        "goleadores": goleadores,
        "forma": forma,
        "estadio": _texto_info(info.get("Stadium")),
        "publico": _texto_info(info.get("Attendance")),
    }
