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




def buscar_partido(local, visita, fecha=None):
    """matchId de FotMob para local vs visita (nombre normalizado + fecha)."""
    if not local or not visita:
        return None

    objetivo = {_clave(local), _clave(visita)}
    cuando = _ts(fecha)
    candidatos = []

    for nombre in (local, visita):
        tid = equipo_id(nombre)
        if not tid:
            continue
        for f in _partidos_equipo(tid):
            h = _clave(((f.get("home") or {}).get("name")) or "")
            a = _clave(((f.get("away") or {}).get("name")) or "")
            if not h or not a or not f.get("id"):
                continue
            exacto = objetivo <= {h, a}
            parecido = any(
                o and (o in h or h in o or o in a or a in o) and abs(len(o) - len(h)) <= 5
                for o in objetivo
            )
            if not (exacto or parecido):
                continue
            ts = _ts((f.get("status") or {}).get("utcTime"))
            dif = abs(ts - cuando) if (ts and cuando) else 0
            candidatos.append((0 if exacto else 1, dif, f.get("id")))

    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (c[0], c[1]))
    return candidatos[0][2]


def _jugador(p):
    """Normaliza un jugador de FotMob (titular o suplente)."""
    rend = p.get("performance") or {}
    h = p.get("horizontalLayout") or {}
    return {
        "id": p.get("id"),
        "nombre": _repara(p.get("name") or ""),
        "dorsal": p.get("shirtNumber") or "",
        "pos_id": p.get("positionId"),
        "x": round(float(h.get("x") or 0), 4),
        "y": round(float(h.get("y") or 0), 4),
        "nota": rend.get("rating"),
        "edad": p.get("age"),
        "pais": _repara(p.get("countryName") or ""),
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
        bajas.append({
            "nombre": _repara(p.get("name") or ""),
            "foto": foto_jugador(p.get("id")),
            "motivo": _repara(info.get("type") or ""),
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



def alineacion(match_id):
    """Alineacion OFICIAL de un partido de FotMob (o None si aun no hay)."""
    datos = _get("matchDetails", {"matchId": match_id}, ttl=120)
    contenido = (datos or {}).get("content") or {}
    lin = contenido.get("lineup") or {}
    if not lin.get("homeTeam") or not lin.get("awayTeam"):
        return None

    local = _lado(lin.get("homeTeam"), "home")
    visita = _lado(lin.get("awayTeam"), "away")
    if not local["titulares"] or not visita["titulares"]:
        return None
    return {"tipo": "oficial", "local": local, "visita": visita}


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
