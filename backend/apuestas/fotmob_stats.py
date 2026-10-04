"""
Estadisticas reales de partidos de FUTBOL via FotMob.
=====================================================

Por que este modulo existe: los markets de corners, tarjetas, faltas y tiros NO
se pueden decidir con el marcador final (3-2 no dice cuantos corners hubo). Se
resolvian con Sofascore, que devuelve 403 desde hace semanas: la funcion
_devolvia None, el pick se quedaba PENDIENTE y a las 72h se marcaba ANULADO.
Consecuencia medida en produccion: 16 picks de corners seguidos con 0 ACIERTOS,
que estadisticamente es practicamente imposible y apuntaba a etiquetas
inventadas, no a resultados reales.

Sofascore (403) y FlashScore (401) no son alternativas: no tienen acceso
publico. FotMob si, y aparte de las stats da la separacion local/visitante, el
primer tiempo y el arbitro.

COBERTURA MEDIDA (96 partidos, 13 ligas de las que aparecen en los picks):
    corners/tarjetas/faltas disponibles en el 75% de los partidos.
    Ponderado por el volumen real de picks: ~86%.
    MLS, Argentina, Brasileirao, Mexico y Nations League: 100%.
    SerieA, Portugal, Ligue 1: 38-50%.
Cuando el dato no esta, estas funciones devuelven None y el resolver deja el
pick como estaba. NUNCA se inventa un numero.
"""
import json
import threading
import time
import unicodedata
from datetime import date, timedelta
from urllib.parse import quote

import requests

BASE = "https://www.fotmob.com/api/data/"
H = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.fotmob.com/",
}

_cache = {}
_lock = threading.Lock()
TTL = 86400  # 24 h: el resultado de un partido ya terminado no cambia nunca

# Metricas que sabemos leer de matchDetails (verificado sobre partidos reales).
# corners -> top_stats del partido; el resto en sus grupos tematicos.
_CLAVES = {
    "corners": ("corners",),
    "yellow_cards": ("yellow_cards",),
    "red_cards": ("red_cards",),
    "fouls": ("fouls",),
    "shots": ("total_shots",),
    "shots_on_target": ("ShotsOnTarget",),
    "offsides": ("Offsides",),
}

# Minimo de partidos por condicion (local / visitante) para afirmar un promedio.
# Con menos de esto el promedio es ruido y se devuelve None.
MIN_POR_CONDICION = 5


def _get(ruta, params=None):
    """GET a la API JSON de FotMob con cache en memoria."""
    clave = ruta + "?" + json.dumps(params or {}, sort_keys=True)
    ahora = time.time()
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < TTL:
            return hit[1]
    try:
        r = requests.get(f"{BASE}{ruta}", params=params, headers=H, timeout=20)
        datos = json.loads(r.content.decode("utf-8", "replace"))
    except Exception:
        datos = None
    with _lock:
        _cache[clave] = (ahora, datos)
    return datos


def _repara(t):
    """FotMob sirve nombres con doble UTF-8 ('Bayern MÃ¼nchen')."""
    if not t:
        return ""
    try:
        return t.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t


def _clave_texto(t):
    """Comparable sin acentos: 'Bayern München' -> 'bayernmunchen'."""
    t = unicodedata.normalize("NFKD", _repara(t or ""))
    return "".join(c for c in t if c.isalnum()).lower()


# === Busqueda de equipos ====================================================

def buscar_equipo(nombre):
    """'Arsenal' -> {'id': 9825, 'name': 'Arsenal'}. None si no lo encuentra."""
    if not nombre:
        return None
    datos = _get("search/suggest", {"term": nombre})
    if not isinstance(datos, list):
        return None
    objetivo = _clave_texto(nombre)
    candidatos = []
    for bloque in datos:
        for s in (bloque or {}).get("suggestions") or []:
            if s.get("type") in ("team", "country") and s.get("id"):
                candidatos.append(s)
    for s in candidatos:
        if _clave_texto(s.get("name")) == objetivo:
            return s
    for s in candidatos:
        n = _clave_texto(s.get("name"))
        if objetivo and n and (objetivo in n or n in objetivo):
            return s
    return candidatos[0] if candidatos else None


# === Stats de un partido ====================================================

def _grupos_stats(detalles, periodo="All"):
    """Aplana matchDetails -> {clave_metrica: [valor_local, valor_visitante]}.

    La API anida las stats en grupos (top_stats, shots, discipline...) y cada
    stat tiene 'stats': [local, visitante]. Se recorren TODOS los grupos porque
    la misma metrica puede aparecer en mas de uno.
    """
    content = detalles.get("content") or {}
    periodos = ((content.get("stats") or {}).get("Periods") or {})
    allev = (periodos.get(periodo) or {}).get("stats") or []
    salida = {}
    for grupo in allev:
        for s in (grupo.get("stats") or []):
            valores = s.get("stats")
            clave = s.get("key")
            if clave and isinstance(valores, list) and len(valores) == 2:
                salida.setdefault(clave, valores)
    return salida


def _a_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def total_estadistica(match_id, metrica, periodo="All"):
    """Total (local+visitante) de una metrica de un partido. int o None.

    metrica: 'corners', 'yellow_cards', 'fouls', 'shots', ...
    Devuelve None si el partido no trae esa stat (fotmob no la publica en
    todas las ligas) o si la respuesta no es utilizable. Nunca estima.
    """
    detalles = _get("matchDetails", {"matchId": str(match_id)})
    if not isinstance(detalles, dict):
        return None
    if not ((detalles.get("general") or {}).get("finished")):
        return None

    claves = _CLAVES.get(metrica, (metrica,))
    datos = _grupos_stats(detalles, periodo)
    total = 0
    encontrado = False
    for clave in claves:
        if clave in datos:
            v = datos[clave]
            a, b = _a_int(v[0]), _a_int(v[1])
            if a is not None and b is not None:
                total += a + b
                encontrado = True
    return total if encontrado else None


def breakdown_equipo(match_id, metrica, home_name=None, away_name=None,
                     periodo="All"):
    """(valor_local, valor_visitante) de una metrica. (None, None) si no hay."""
    detalles = _get("matchDetails", {"matchId": str(match_id)})
    if not isinstance(detalles, dict):
        return None, None
    claves = _CLAVES.get(metrica, (metrica,))
    datos = _grupos_stats(detalles, periodo)
    for clave in claves:
        if clave in datos:
            v = datos[clave]
            a, b = _a_int(v[0]), _a_int(v[1])
            if a is not None and b is not None:
                return a, b
    return None, None


def arbitro(match_id):
    """{'id': int, 'nombre': str} del arbitro del partido, o None."""
    detalles = _get("matchDetails", {"matchId": str(match_id)})
    if not isinstance(detalles, dict):
        return None
    info = (((detalles.get("content") or {}).get("matchFacts") or {})
            .get("infoBox") or {})
    ref = info.get("Referee") or {}
    if not ref.get("id"):
        return None
    return {"id": ref.get("id"), "nombre": _repara(ref.get("text") or "")}


def formacion(match_id):
    """Formacion del local y del visitante. (None, None) si no hay.

    OJO: 'formation' llega como STRING ('4-3-3'), no como dict. Se normalizing
    a texto plano; si venia como dict se queda con su 'formation'.
    """
    detalles = _get("matchDetails", {"matchId": str(match_id)})
    if not isinstance(detalles, dict):
        return None, None
    lu = (detalles.get("content") or {}).get("lineup") or {}
    salida = []
    for lado in ("homeTeam", "awayTeam"):
        v = (lu.get(lado) or {}).get("formation")
        if isinstance(v, dict):
            v = v.get("formation")
        salida.append(v or None)
    return tuple(salida)


def clima(match_id):
    """Clima del partido: temperatura, viento, lluvia. {} si no hay."""
    detalles = _get("matchDetails", {"matchId": str(match_id)})
    if not isinstance(detalles, dict):
        return {}
    return (detalles.get("content") or {}).get("weather") or {}


# === Localizar el partido de un pick ======================================

def partido_por_equipos(home_name, away_name, fecha=None):
    """Info del partido entre esos dos equipos. dict o None.

    Devuelve {'id', 'home_name', 'away_name', 'home_score', 'away_score',
              'finished'} con el marcador real.

    ANTES recorria /matches dia a dia desde la fecha del pick. Fallaba: para
    partidos de hace dias /matches no devolvia esas ligas y el resultado era
    None (medido: 0 de 12 picks pendientes resueltos). Ahora se usa
    teams?id= -> fixtures, que ya trae local, visitante, fecha y marcador de
    golpe, y ademas esta cacheado 24 h por equipo.
    """
    if not home_name or not away_name:
        return None
    h, a = _clave_texto(home_name), _clave_texto(away_name)

    # Backoff en dias: si el pick tiene fecha, se priorizan los partidos de esa
    # vicinity; si no, se mira el historial reciente del equipo.
    dias_preferidos = []
    if fecha:
        try:
            dias_preferidos = [
                (date.fromisoformat(str(fecha)[:10]) - timedelta(days=o)).isoformat()
                for o in range(0, 5)
            ]
        except ValueError:
            dias_preferidos = []

    eq = buscar_equipo(home_name)
    if not eq or not eq.get("id"):
        return None
    datos = _get("teams", {"id": str(eq["id"])})
    if not isinstance(datos, dict):
        return None
    fixtures = (((datos.get("fixtures") or {}).get("allFixtures") or {})
                .get("fixtures") or [])

    candidatos = []
    for f in fixtures:
        hn = _clave_texto((f.get("home") or {}).get("name"))
        an = _clave_texto((f.get("away") or {}).get("name"))
        if (hn == h and an == a) or (hn == a and an == h):
            dia = str((f.get("status") or {}).get("utcTime") or "")[:10]
            prioritario = dias_preferidos.index(dia) if dia in dias_preferidos else 99
            candidatos.append((prioritario, f))

    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0])
    f = candidatos[0][1]

    def _sc(t):
        return _a_int((t or {}).get("score"))

    return {
        "id": f.get("id"),
        "home_name": (f.get("home") or {}).get("name"),
        "away_name": (f.get("away") or {}).get("name"),
        "home_score": _sc(f.get("home")),
        "away_score": _sc(f.get("away")),
        "finished": bool((f.get("status") or {}).get("finished")),
        "utc": (f.get("status") or {}).get("utcTime"),
    }


def total_estadistica_de_pick(pick, metrica, periodo="All"):
    """Total de una metrica para el partido de un pick. int o None.

    Atajo usado por el resolver: dado el dict del pick (con home_name,
    away_name y eventDate), devuelve el total de la metrica o None.
    """
    info = partido_por_equipos(
        pick.get("home_name"), pick.get("away_name"), pick.get("eventDate")
    )
    if not info or not info.get("id"):
        return None
    return total_estadistica(info["id"], metrica, periodo)


# === Promedios local / visitante ===========================================

def _promedio(vals):
    return round(sum(vals) / len(vals), 2) if vals else None


def historial_equipo(equipo_id, n=10):
    """Ultimos N partidos TERMINADOS con id y stats. [(match_id, stats)].

    Descarga teams?id= una vez y extrae los partidos ya jugados de
    fixtures.allFixtures. De cada partido pide matchDetails para sacar las
    stats. Un partido sin stats se SALTA (reduce la muestra) en vez de
    inventar un cero.
    """
    datos = _get("teams", {"id": str(equipo_id)})
    if not isinstance(datos, dict):
        return []
    fx = ((datos.get("fixtures") or {}).get("allFixtures") or {})
    partidos = [p for p in (fx.get("fixtures") or [])
                if (p.get("status") or {}).get("finished")]
    partidos.sort(key=lambda p: (p.get("status") or {}).get("utcTime") or "",
                  reverse=True)

    salida = []
    for p in partidos:
        if len(salida) >= n:
            break
        mid = p.get("id")
        if not mid:
            continue
        detalles = _get("matchDetails", {"matchId": str(mid)})
        if not isinstance(detalles, dict):
            continue
        st = _grupos_stats(detalles, "All")
        if not st:
            continue  # el partido no trae stats: no cuenta como muestra
        es_local = (p.get("home") or {}).get("id") == equipo_id
        salida.append({
            "match_id": mid,
            "local": es_local,
            "rival": str((p.get("opponent") or {}).get("name") or "")[:40],
            "stats": st,
        })
    return salida


# Metricas que se promedian y el texto que la IA entendera.
_METRICAS = (
    ("corners", "corners"),
    ("yellow_cards", "tarjetas amarillas"),
    ("fouls", "faltas"),
    ("shots", "tiros"),
    ("shots_on_target", "tiros a puerta"),
)


def perfil_equipo(nombre, n=10):
    """Promedios de un equipo separados LOCAL / VISITANTE / GLOBAL.

    Devuelve {'equipo','nombre','n','global':{metrica:avg},
              'local':{...},'visitante':{...},'n_local','n_visitante'}
    Si falta la muestra minima (MIN_POR_CONDICION) ese corte queda vacio y la
    IA lo ve como 'sin datos' en vez de recibir un promedio inventado.
    """
    eq = buscar_equipo(nombre)
    if not eq or not eq.get("id"):
        return None
    # search/suggest devuelve el id como STRING ("9825") y fixtures lo da como
    # INT. Sin castear, la comparacion home.id == equipo_id era SIEMPRE falsa y
    # todos los partidos se contaban como visitante (bug detectado al ver
    # 'Arsenal local: sin muestra suficiente (0 partidos)' con 12 de visitante).
    try:
        equipo_id = int(eq["id"])
    except (TypeError, ValueError):
        return None
    hist = historial_equipo(equipo_id, n=n)
    if not hist:
        return None

    cortes = {"global": [], "local": [], "visitante": []}
    for h in hist:
        destino = "local" if h["local"] else "visitante"
        cortes["global"].append(h)
        cortes[destino].append(h)

    def promedios(partidos):
        out = {}
        for clave, _etiqueta in _METRICAS:
            vals = []
            for h in partidos:
                v = h["stats"].get(clave)
                if isinstance(v, list) and len(v) == 2:
                    a, b = _a_int(v[0]), _a_int(v[1])
                    if a is not None and b is not None:
                        # el valor A FAVOR del equipo: indice 0 si es local
                        vals.append(a if h["local"] else b)
            out[clave] = _promedio(vals)
        return out

    return {
        "equipo": eq.get("name") or nombre,
        "n": len(hist),
        "global": promedios(cortes["global"]),
        "local": promedios(cortes["local"])
        if len(cortes["local"]) >= MIN_POR_CONDICION else None,
        "visitante": promedios(cortes["visitante"])
        if len(cortes["visitante"]) >= MIN_POR_CONDICION else None,
        "n_local": len(cortes["local"]),
        "n_visitante": len(cortes["visitante"]),
    }


def contexto_para_ia(home_name, away_name, n=10):
    """Bloque de texto con los promedios REALES para meter en el prompt.

    Esto es lo que la IA no tenia: no solo 'gano 2-1' sino CUANTOS corners,
    tarjetas y faltas genera de local y fuera. Con eso puede razonar sobre la
    linea del mercado en vez de intuir.
    """
    try:
        ph = perfil_equipo(home_name, n)
        pa = perfil_equipo(away_name, n)
    except Exception:
        return ""
    if not ph or not pa:
        return ""

    def fmt(perfil, condicion):
        # OJO: perfil[condicion] puede ser None cuando no hay muestra minima.
        # No es un fallo, es "no sabemos": se dice explicitamente para que la IA
        # no rellene el hueco con una suposicion.
        if not perfil or not perfil.get(condicion):
            n = (perfil or {}).get(
                "n_local" if condicion == "local" else "n_visitante", 0)
            nombre = (perfil or {}).get("equipo", "?")
            return (f"  {nombre} ({condicion}): sin muestra suficiente "
                    f"({n} partidos)")
        lineas = []
        for clave, texto in _METRICAS:
            val = perfil[condicion].get(clave)
            if val is not None:
                lineas.append(f"{texto} {val}")
        return f"  {perfil['equipo']} ({condicion}): " + ", ".join(lineas)

    partes = [
        "",
        "ESTADISTICAS REALES (promedios de los ultimos "
        f"{ph.get('n') or n} partidos, calculados partido a partido):",
        fmt(ph, "local"),
        fmt(ph, "visitante"),
        fmt(pa, "local"),
        fmt(pa, "visitante"),
    ]
    # Proyeccion explicita: lo que el local genera + lo que el visitante genera.
    try:
        _proyecciones(partes,
                      (ph.get("local") or {}).get("corners"),
                      (pa.get("visitante") or {}).get("corners"),
                      "corners total")
        _proyecciones(partes,
                      (ph.get("local") or {}).get("yellow_cards"),
                      (pa.get("visitante") or {}).get("yellow_cards"),
                      "tarjetas")
        _proyecciones(partes,
                      (ph.get("local") or {}).get("fouls"),
                      (pa.get("visitante") or {}).get("fouls"),
                      "faltas")
    except Exception:
        pass

    partes.append(
        "USA ESTAS MEDIAS para decidir: si la linea del mercado queda por debajo "
        "de la proyeccion, el lado Over tiene valor; si queda muy por encima, "
        "Under. Si un equipo promedia mucho mas de local que fuera, inclinate "
        "por su condicion de local. Si no hay muestra suficiente, no inventes "
        "y elige otro mercado."
    )
    return "\n".join(partes)


def _proyecciones(partes, valor_local, valor_visitante, etiqueta):
    """Anade la proyeccion 'local + visitante' si ambos lados tienen muestra."""
    if valor_local is not None and valor_visitante is not None:
        partes.append(
            f"  -> PROYECCION {etiqueta} = {valor_local} (local) + "
            f"{valor_visitante} (visitante) = "
            f"{round(valor_local + valor_visitante, 2)}"
        )
