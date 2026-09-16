"""
DASHBOARD - Motor de pronosticos automaticos de 3SIXTYBETS.

Cada dia la IA genera picks para los partidos disponibles (todos los deportes)
usando SOLO los mercados definidos en MERCADOS_POR_DEPORTE. Reglas clave:
- Nunca repetir el mismo mercado en los diferentes/mismos partidos.
- Variar las opciones: no solo 1X2 o goles; buscar la apuesta mas facil de
  acertar con valor.
- Los picks se guardan en MySQL (tabla ai_picks) y se muestran en el dashboard.
- Al finalizar un partido se resuelve el pick (ACIERTO / FALLO). En el
  dashboard solo se muestran los ACERTADOS en el apartado de aciertos.
"""

import json
import os
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import db

# Cuota minima aceptada para mostrar/generar un pick (todo debe estar ARRIBA de 1.20)
ODDS_MINIMA = 1.20
# Los acertados del dia anterior se muestran solo hasta las 23:00 Nicaragua
HORA_CORTE_ACERTADOS = 23

# Ligas "grandes": ~70% de los picks del dia deben venir de aqui.
LIGAS_GRANDES = {"eng.1", "esp.1", "ita.1", "ger.1", "fra.1"}
CUOTA_OTRAS = 0.30   # resto de ligas/deportes: maximo ~30% del total (min 2)

# Validacion empirica: el outcome del pick debe ocurrir en al menos este
# % de los ultimos 10 partidos de cada equipo (promedio de ambos). Ej: si
# el pick es over 2.5, ambos equipos deben haber hecho over 2.5 en >=60%
# de sus ultimos 10 para publicar la recomendacion.
FRECUENCIA_MINIMA = 0.60

# --- Calidad por historico (track record) ---
# Una familia de mercado con >=RESUELTOS muestras y efectividad >= este % puede
# repetirse mercado dentro de la jornada (la diversidad estricta cuesta aciertos).
REUTILIZAR_EFECTIVIDAD = 60
REUTILIZAR_RESUELTOS = 8
# Familia con suficientes muestras y efectividad por debajo de esto: vetada.
VETO_EFECTIVIDAD = 45
VETO_RESUELTOS = 10
# Best-of-N: si un candidato alcanza esta frecuencia empirica, no se piden mas.
BEST_OF_N = 3
PARADA_TEMPRANA_PROM = 0.75

TZ_NICARAGUA = db.TZ_NICARAGUA


def _hora_nicaragua():
    return datetime.now(timezone.utc).astimezone(TZ_NICARAGUA)


def _cuota_valida(pick):
    """True si el pick no tiene cuota (se permite) o su cuota es > ODDS_MINIMA."""
    odds = pick.get("odds")
    if odds is None:
        return True
    try:
        return float(odds) > ODDS_MINIMA
    except (TypeError, ValueError):
        return True


# Frases que delatan picks generados SIN datos reales (basura que no se muestra)
_FRASES_SIN_DATOS = (
    "sin datos", "no disponible", "no disponibles", "no especificad",
    "no identificad", "no verifiable", "sin cuota", "linea no disponible",
    "n/d", "no apostar", "prepick", "sin recomendacion", "sin recomendación",
    "sin pick",
)

_NOMBRES_INVALIDOS = {"", "?", "n/a", "na", "jugador a", "jugador b",
                      "equipo a", "equipo b", "local", "visitante",
                      "team a", "team b", "home", "away", "jugador 1", "jugador 2"}


def _nombre_valido(nombre) -> bool:
    return bool(nombre) and str(nombre).strip().lower() not in _NOMBRES_INVALIDOS


def _limpiar_artefactos(txt) -> str:
    """Quita marcas de citacion del modelo ([[2]], [|2|], [(2|], 【2】...)."""
    if not txt:
        return txt or ""
    txt = str(txt)
    # Admite bloques dobles [[1]], [|2|], [(2|], 【2】, [[3]]... (el contenido
    # puede incluir corchetes, por eso el interior usa "cualquier cosa que no
    # sea cierre" en vez de solo espacios/digitos).
    txt = re.sub(r"[\[\(\{【]{1,2}[^\]\)\}】]{0,8}[\]\)\}】]{1,3}", "", txt)
    txt = re.sub(r"\s{2,}", " ", txt)
    return txt.strip()


def _porque_no_duplicado(porque, stats, rationale) -> str:
    """El 'porque' NO debe repetir una linea del cuadro de datos (bug visual:
    la linea verde decia exactamente lo mismo que el primer punto de stats).

    Si 'porque' duplica una stat, se sustituye por la primera frase del
    razonamiento (informacion que NO esta en el cuadro de datos).
    """
    porque = (str(porque) or "").strip()
    if not porque:
        return ""
    palabras = set(re.findall(r"[a-z0-9]{3,}", porque.lower()))
    for s in (stats or []):
        st = str(s or "")
        if not st:
            continue
        st_norm = re.sub(r"[\[\(\{【]{1,2}[^\]\)\}】]{0,8}[\]\)\}】]{1,3}", "", st)
        if porque.lower()[:60] in st_norm.lower() or st_norm.lower()[:60] in porque.lower():
            duplicado = True
            break
        if palabras and len(palabras & set(re.findall(r"[a-z0-9]{3,}", st_norm.lower()))) / len(palabras) > 0.6:
            duplicado = True
            break
    else:
        duplicado = False
    if not duplicado:
        return porque
    # Fallback: primera frase del razonamiento (no aparece en el cuadro)
    razon = (str(rationale) or "").strip()
    if razon:
        frase = re.split(r"(?<=[.!?])\s", razon)[0]
        frase = _limpiar_artefactos(frase)
        if frase and len(frase) <= 160:
            return frase
        return frase[:157] + "..." if frase else ""
    return ""


def _titulo_contradice(pick: dict, p: dict) -> bool:
    """True si el titulo contradice la seleccion de doble oportunidad.

    Bug real detectado: titulo 'Alaves no pierde (1X)' con seleccion '2X'
    (apuestas opuestas). Solo aplica a doble oportunidad; otros mercados
    devuelven False.
    """
    sel = str(pick.get("selection") or "").upper().replace(" ", "")
    codigo_sel = {"2X": "X2", "21": "12"}.get(sel, sel)
    if codigo_sel not in ("1X", "X2", "12"):
        return False
    titulo = str(pick.get("titulo") or "").upper()

    # Codigo explicito en el titulo (1X, 2X, X2, 12, 21)
    m = re.search(r"\b(1X|X2|2X|12|21)\b", titulo)
    if m:
        cod = {"2X": "X2", "21": "12"}.get(m.group(1), m.group(1))
        return cod != codigo_sel

    # Frases "X no pierde" / "X no gana" con el nombre del equipo
    local = str(p.get("home_name") or "").upper()
    visitante = str(p.get("away_name") or "").upper()
    for nombre, cod_no_pierde in ((local, "1X"), (visitante, "X2")):
        if nombre and nombre in titulo and "NO PIERDE" in titulo:
            return cod_no_pierde != codigo_sel
    for nombre, cod_no_gana in ((local, "X2"), (visitante, "1X")):
        if nombre and nombre in titulo and "NO GANA" in titulo:
            return cod_no_gana != codigo_sel
    return False


def _texto_sin_datos(texto) -> bool:
    t = (texto or "").lower()
    return any(f in t for f in _FRASES_SIN_DATOS)


def _sin_acentos(s: str) -> str:
    """Normaliza texto quitando acentos (para comparar sin importar tildes)."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", str(s or ""))
        if unicodedata.category(c) != "Mn"
    ).lower()


# Frases con las que la IA reconoce que el pick es invalido pero lo emite
# igual (bug real: pick de Shohei Ohtani en un partido White Sox vs Guardians,
# con el texto "Alternativa invalida: Ohtani no juega este partido").
_FRASES_CONTRADICCION = (
    "no juega", "no jugara", "no participara", "no participa",
    "no pertenece", "no forma parte", "no esta en el partido",
    "no esta en este partido", "alternativa invalida",
    "alternativa no valida", "pick invalido", "apuesta invalida",
    "cuota no juega",
)


def _pick_se_declara_invalido(pick: dict) -> bool:
    """True si la IA admite en alguno de sus textos que el pick es invalido."""
    texto = _sin_acentos(
        " ".join(
            str(pick.get(k) or "")
            for k in ("titulo", "porque", "rationale", "selection")
        )
    )
    return any(f in texto for f in _FRASES_CONTRADICCION)


# Mercados de props individuales: la validez depende de que el jugador
# nombrado juegue realmente ese partido.
_MERCADOS_JUGADOR = (
    "por jugador", "del jugador", "bases totales", "hits totales",
    "hr totales", "home runs del", "carreras anotadas del",
    "ponches del", "strikeouts del", "bases por",
)

# Palabras del mercado que se limpian al extraer el nombre del jugador del titulo
_PALABRAS_MERCADO_TITULO = (
    "bases totales", "hits totales", "hr totales", "home runs",
    "carreras anotadas", "ponches", "strikeouts", "total de", "totales",
    "por jugador", "del jugador", "incl extra innings", "incl",
    "extra innings", "bases", "hits", "carreras",
)


def _extraer_jugador(titulo: str) -> str:
    """Extrae el nombre del jugador de un titulo tipo 'Shohei Ohtani bases totales: Over 2.5'."""
    t = str(titulo or "")
    t = t.split(":")[0]  # quitar la seleccion ('Over 2.5')
    t_norm = _sin_acentos(t)
    for palabra in _PALABRAS_MERCADO_TITULO:
        t_norm = t_norm.replace(palabra, " ")
    nombre = " ".join(t_norm.split())
    return nombre


def _jugador_valido_en_partido(pick: dict, p: dict) -> bool:
    """True si el pick de prop-de-jugador nombra a alguien del partido.

    Compara los tokens del nombre extraido del titulo contra los jugadores
    del detalle ESPN (key_players / rosters). Si no se puede extraer jugador
    o no hay datos del partido, devuelve True para no bloquear aqui (otros
    gates ya filtran basura).
    """
    texto = _sin_acentos(f"{pick.get('titulo') or ''} {pick.get('market') or ''}")
    if not any(m in texto for m in _MERCADOS_JUGADOR):
        return True  # no es prop de jugador: no aplica

    jugador = _extraer_jugador(pick.get("titulo"))
    tokens = {t for t in re.findall(r"[a-z]{3,}", jugador)}
    if not tokens:
        return True

    try:
        import sports

        detail = sports.get_game_detail(p["sport"], p["event_id"])
    except Exception:
        return True
    nombres = [
        str(j.get("name") or "")
        for j in (detail.get("key_players") or [])
        if isinstance(j, dict)
    ]
    if not nombres:
        return True  # sin datos de jugadores: no bloquear
    for nombre in nombres:
        nt = set(re.findall(r"[a-z]{3,}", _sin_acentos(nombre)))
        if tokens & nt:
            return True
    return False


def _pick_calidad_ok(pick: dict) -> bool:
    """Gate de calidad para MOSTRAR un pick (pendientes y aciertos).

    Rechaza: equipos '?', nombres genericos, sin cuota, cuota <= 1.20
    y rationale/titulo con frases de 'sin datos'.
    """
    if not (_nombre_valido(pick.get("homeName")) and _nombre_valido(pick.get("awayName"))):
        return False
    if not _cuota_valida(pick) or pick.get("odds") is None:
        return False
    if _texto_sin_datos(pick.get("rationale")) or _texto_sin_datos(pick.get("titulo")):
        return False
    if _texto_sin_datos(pick.get("eventName")):
        return False
    return True

# ============================================================
# CUOTAS REALES (odds-api.io)
# ============================================================

ODDS_API_KEY = (
    os.getenv("ODDS_API_KEY")
    or os.getenv("AI36_ODDS_API_KEY")
    or "629022e8c84bef4696b26fd180f45a503d5a5aec633e826ef3f051984648ae4b"
)
ODDS_API_BASE = "https://api.odds-api.io/v3"
# Plan free: solo 2 bookmakers permitidos por la cuenta: Bet365 y Winpot MX.
# (1xbet/Stake daban 403 "Access denied" y por eso faltaban cuotas reales.)
ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "Bet365")
# Mapeo de nuestros deportes a los slugs de odds-api.io
ODDS_SPORT_SLUGS = {
    "soccer": "football",
    "nba": "basketball",
    "mlb": "baseball",
    "tennis": "tennis",
}

_odds_cache = {}  # clave -> (timestamp, data)
_ODDS_CACHE_TTL = 600  # 10 min para cuotas de un evento (respeta rate limit)
_ODDS_EVENTS_TTL = 1800  # 30 min para la lista de eventos (la cuota diaria es de 500 req)
_odds_bloqueado_hasta = 0  # backoff cuando la API responde 429


def _odds_bloqueado():
    return time.time() < _odds_bloqueado_hasta


def _norm_texto(s: str) -> str:
    s = (s or "").lower().strip()
    reemplazos = {
        "Ã¡": "a", "Ã©": "e", "Ã­": "i", "Ã³": "o", "Ãº": "u", "Ã¼": "u", "Ã±": "n",
    }
    for k, v in reemplazos.items():
        s = s.replace(k, v)
    return s


def _odds_request(path: str, params: dict):
    if _odds_bloqueado():
        return None
    try:
        import requests

        params = {"apiKey": ODDS_API_KEY, **params}
        r = requests.get(f"{ODDS_API_BASE}{path}", params=params, timeout=20)
        if r.status_code != 200:
            print(f"[Dashboard] odds-api.io {path} -> {r.status_code}: {r.text[:150]}")
            # 429 (limite diario/horario): pausar 15 min para no quemar cuota
            if r.status_code == 429:
                global _odds_bloqueado_hasta
                _odds_bloqueado_hasta = time.time() + 900
                return None
            # 403 de bookmakers: devolver los permitidos por la cuenta
            if r.status_code == 403 and "Allowed:" in r.text:
                permitidos = r.text.split("Allowed:", 1)[1]
                permitidos = permitidos.split(".")[0].strip()
                return {"__permitidos__": [p.strip() for p in permitidos.split(",")]}
            return None
        return r.json()
    except Exception as exc:
        print(f"[Dashboard] odds-api.io {path} error: {exc}")
        return None


def _bookmakers_permitidos(event_id=None):
    """Bookmakers seleccionados en la cuenta (free plan: max 2)."""
    return _odds_request("/odds", {"eventId": event_id, "bookmakers": ODDS_BOOKMAKERS})


def _odds_eventos(sport_slug: str):
    """Lista de eventos de un deporte, con cache de 30 min (ahorra cuota diaria)."""
    ahora = time.time()
    cached = _odds_cache.get(f"events:{sport_slug}")
    if cached and ahora - cached[0] < _ODDS_EVENTS_TTL:
        return cached[1]
    data = _odds_request("/events", {"sport": sport_slug})
    if isinstance(data, list):
        _odds_cache[f"events:{sport_slug}"] = (ahora, data)
        return data
    # Respuesta vacia/fallida: cache corto para no machacar la API
    _odds_cache[f"events:{sport_slug}"] = (ahora - _ODDS_CACHE_TTL + 60, [])
    return []


def _odds_evento(event_id):
    """Mercados reales del evento, con cache. Autodetecta los bookmakers
    permitidos por la cuenta si la seleccion inicial es rechazada (403)."""
    ahora = time.time()
    cached = _odds_cache.get(f"odds:{event_id}")
    if cached and ahora - cached[0] < _ODDS_CACHE_TTL:
        return cached[1]

    global ODDS_BOOKMAKERS
    books = ODDS_BOOKMAKERS
    mercados = None

    # Hasta 2 intentos: el 2º usa los bookmakers permitidos que reporte la API
    for _ in range(2):
        data = _odds_request("/odds", {"eventId": event_id, "bookmakers": books})
        if data and data.get("__permitidos__"):
            permitidos = [b for b in data["__permitidos__"] if b]
            if permitidos:
                books = ",".join(permitidos)
                ODDS_BOOKMAKERS = books  # recordar para las siguientes llamadas
                continue
        if isinstance(data, dict):
            for bookmaker in (data.get("bookmakers") or {}).values():
                if isinstance(bookmaker, list) and bookmaker:
                    mercados = bookmaker
                    break
        break

    # Solo cacheamos resultados validos; los fallidos se reintentan
    if mercados is not None:
        _odds_cache[f"odds:{event_id}"] = (ahora, mercados)
    return mercados


def _cuotas_sportradar(sport: str, home_name: str, away_name: str) -> str:
    """Fallback: cuotas reales desde Sportradar Odds Comparison (trial).

    Solo soccer. Devuelve "" si la key es invalida o no hay match.
    """
    try:
        from engine import sportradar

        fecha = _hora_nicaragua().date().isoformat()
        evento, mercados = sportradar.cuotas_partido(sport, home_name, away_name, fecha)
        if not evento or not mercados:
            # Segundo intento sin filtro de fecha (por diferencias de zona horaria)
            evento, mercados = sportradar.cuotas_partido(sport, home_name, away_name)
        if not evento or not mercados:
            return ""
        return sportradar.formato_cuotas(evento, mercados)
    except Exception as exc:
        print(f"[Dashboard] sportradar cuotas error: {exc}")
        return ""


def _cuotas_reales(sport: str, home_name: str, away_name: str) -> str:
    """Devuelve un texto con las cuotas reales del partido.

    Fuente primaria: odds-api.io (Bet365). Fallback: Sportradar
    Odds Comparison (cuotas de multiples bookmakers).
    Matching difuso por nombre de equipos contra los eventos del deporte.
    Devuelve "" si no hay coincidencia o no hay cuotas.
    """
    slug = ODDS_SPORT_SLUGS.get(sport)
    if not slug:
        return ""

    eventos = _odds_eventos(slug)
    nh, na = _norm_texto(home_name), _norm_texto(away_name)

    def _coincide(a: str, b: str) -> bool:
        a, b = _norm_texto(a), _norm_texto(b)
        return bool(a) and bool(b) and (a == b or a in b or b in a)

    evento = None
    for e in eventos:
        if e.get("status") not in ("pending", "live"):
            continue
        if _coincide(e.get("home", ""), home_name) and _coincide(
            e.get("away", ""), away_name
        ):
            evento = e
            break

    if not evento:
        return _cuotas_sportradar(sport, home_name, away_name)

    mercados = _odds_evento(evento.get("id"))
    if not mercados:
        return _cuotas_sportradar(sport, home_name, away_name)

    lineas = [f"CUOTAS REALES ({evento['home']} vs {evento['away']}, odds-api.io):"]
    for m in mercados[:20]:
        nombre = m.get("name", "?")
        for o in m.get("odds", [])[:2]:
            pares = [f"{k}={v}" for k, v in o.items() if v not in (None, "")]
            lineas.append(f"- {nombre}: {', '.join(pares)}")
    lineas.append(
        "USA estas cuotas reales para calcular valor; la linea que elijas debe "
        "respetar los minimos del catalogo."
    )
    return "\n".join(lineas)

# ============================================================
# MERCADOS DEFINIDOS POR DEPORTE (unico catalogo permitido)
# ============================================================

MERCADOS_FUTBOL = [
    "1X2 (equipo A o B)",
    "Over de goles (minimo 1.25 segun la cuota)",
    "Doble oportunidad (1X, X2, 12)",
    "Total tiros de esquina, minimo 7.5 corners, cuota minima 1.25",
    "Total tiros de esquina de equipo A o B, minimo 3.5",
    "Primera mitad tiros de esquina, minimo 3.5",
    "Ambos equipos +4 tiros de esquina cada uno (SI/NO)",
    "Ambos equipos +2 tiros de esquina cada uno (SI/NO)",
    "Ambos equipos +1 tarjeta cada uno (SI/NO)",
    "Ambos equipos +2 tarjetas cada uno (SI/NO)",
    "Ambos equipos marcan",
    "Apuesta sin empate",
    "Handicap europeo o asiatico equipo A o B (min +3.5 / max -2.5)",
    "Equipo A total de goles over 0.5",
    "Equipo B total de goles over 0.5",
    "Multigoles",
    "Equipo A gana cualquier mitad (SI/NO)",
    "Equipo B gana cualquier mitad (SI/NO)",
    "Cualquier equipo gana",
    "Ambos equipos marcan o 2.5 goles",
    "Total fueras de juego equipo A o B, minimo 2.5",
    "Tiros a puerta del jugador, minimo 0.5 o 1.5",
    "Tiros en general del jugador",
    "Jugador que marca o asiste",
    "Over de tarjetas",
    "Goleador en cualquier momento",
    "Equipo A o B gana la primera mitad",
    "Props de jugadores",
    "Under/Over de tiros generales equipo A o B",
    "Under/Over de tiros a puerta equipo A o B",
    "Total de tiros del partido (minimo 15)",
    "Total de tiros a puerta del partido (minimo 6)",
    "Total de tarjetas amarillas del partido (minimo 2.5)",
    "Total de faltas equipo A o B",
    "Under/Over de tarjetas equipo A o B",
]

MERCADOS_NBA = [
    "Ganador (incl. prorroga)",
    "Handicap (min positivo +25.5 / max negativo -1)",
    "Total de puntos (incl. prorroga) - USAR SIEMPRE EL UNDER MAS BAJO DEL PARTIDO COMO PRIORIDAD (ej: si el under mas bajo es 210.5, usar ese, NO 220.5)",
    "Total de puntos del equipo A",
    "Total de puntos del equipo B",
    "Minimo de puntos del jugador",
    "Minimo de rebotes del jugador",
    "Minimo de asistencias del jugador",
    "Minimo de triples anotados del jugador",
    "Minimo puntos+rebotes del jugador",
    "Minimo puntos+asistencias del jugador",
    "Minimo asistencias+rebotes del jugador",
    "Minimo puntos+rebotes+asistencias del jugador",
    "Jugador hace doble-doble (SI/NO)",
    "Jugador hace triple-doble (SI/NO)",
    "Ambos equipos anotaran 100 puntos (SI/NO)",
    "Ambos equipos anotaran 110 puntos (SI/NO)",
    "Ambos equipos anotaran OVER 100 puntos Y equipo A o B gana (SI/NO)",
    "Ambos equipos anotaran OVER 110 puntos Y equipo A o B gana (SI/NO)",
    "Ambos equipos anotaran UNDER 110 puntos Y equipo A o B gana (SI/NO)",
    "Total asistencias del equipo A o B",
    "Total robos del equipo A o B",
    "Total triples del equipo A o B",
    "Total rebotes del equipo A o B",
    "1er cuarto total de puntos",
    "Equipo A o B gana la primera mitad",
    "Carrera a 10 puntos equipo A o B",
    "Carrera a 20 puntos equipo A o B",
    "Primera mitad - total de puntos",
    "Primera mitad - equipo A o B total de puntos",
    "Primer cuarto total de puntos",
    "Primer cuarto - handicap",
]

MERCADOS_MLB = [
    "Ganador incl extra innings",
    "Totales incl extra innings",
    "Handicap incl extra innings (positivo o negativo)",
    "Ganador y total incl extra innings",
    "Hits mas de/menos de incl extra innings",
    "Equipo A hits mas de/menos de incl extra innings",
    "Equipo B hits mas de/menos de incl extra innings",
    "Bases totales por jugador incl extra innings",
    "Hits totales del jugador incl extra innings",
    "HR totales del jugador incl extra innings",
    "Strikeouts (SO) del jugador incl extra innings",
    "Equipo A totales de runs over/under",
    "Equipo B totales de runs over/under",
    "Hits + carreras + RBIs del jugador incl extra innings",
    "Lanzador total hits permitidos incl extra innings",
]

MERCADOS_TENIS = [
    "Ganador",
    "Juegos",
    "Sets",
    "Handicap",
    "Primer set ganador",
    "Segundo set ganador",
    "Handicap de sets",
    "Handicap de juegos",
    "Total juegos (priorizar SIEMPRE el under mas bajo del mercado)",
    "Marcador exacto",
    "Jugador A total juegos",
    "Jugador B total juegos",
    "Gana un set jugador A",
    "Gana un set jugador B",
    "Ambos jugadores ganan un set",
    "Doble resultado (1er set/partido)",
    "Sets exactos",
    "Hitos de aces totales",
    "Aces totales jugador A",
    "Aces totales jugador B",
    "Breaks totales",
    "Jugador A total de breaks",
    "Jugador B total de breaks",
    "Hitos de doble faltas",
    "Hitos de doble faltas jugador A",
    "Hitos de doble faltas jugador B",
    "Primer set handicap de juegos",
    "Primer set total juegos under/over",
    "Segundo set total juegos under/over",
    "Encuentro total tie breaks",
]

MERCADOS_POR_DEPORTE = {
    "soccer": ("Futbol", MERCADOS_FUTBOL),
    "nba": ("NBA", MERCADOS_NBA),
    "mlb": ("MLB", MERCADOS_MLB),
    "tennis": ("Tenis", MERCADOS_TENIS),
}

DEPORTES_DASHBOARD = list(MERCADOS_POR_DEPORTE.keys())

# ============================================================
# PROMPT MAESTRO (usado por LAS DOS IAS: 365AI y Demian)
# ============================================================


def catalogo_texto():
    lineas = []
    for sport, (label, mercados) in MERCADOS_POR_DEPORTE.items():
        lineas.append(f"{label.upper()}:")
        for m in mercados:
            lineas.append(f"- {m}")
        lineas.append("")
    return "\n".join(lineas)


PROMPT_PICKS = """Eres 3SIXTYBETS AI, analista cuantitativo de apuestas deportivas.

SOLO PUEDES USAR ESTOS PICKS DEFINIDOS PARA CADA DEPORTE:

""" + catalogo_texto() + """
REGLAS OBLIGATORIAS:
1. USA SOLO los mercados listados arriba. NUNCA inventes mercados.
2. Los textos del catalogo son INSTRUCCIONES/REGLAS del mercado (minimos de
   linea, cuota minima, limites de handicap, etc.), NO texto literal para el
   usuario. Interpretalos: eligen la linea concreta que cumpla esas reglas.
3. Dentro del MISMO partido nunca repitas un mercado. Entre partidos del dia,
   evita repetir mercado SALVO que el historial de rendimiento de abajo
   demuestre que esa familia es de las mas efectivas (60%+ de aciertos):
   en ese caso repetirla esta PERMITIDO y es preferible a forzar un mercado
   raro. NUNCA elijas una familia marcada como vetada.
4. SIEMPRE ve variando las opciones: no te centres solo en 1X2 o goles.
5. Busca SIEMPRE la apuesta mas FACIL de acertar CON VALOR (cuota justa vs probabilidad real).
6. Respeta los minimos indicados (cuotas minimas, handicap minimo, under mas bajo en NBA/tenis).
7. Responde EXCLUSIVAMENTE con un JSON valido, sin texto extra, con esta forma exacta:
{
  "market": "<nombre del mercado del catalogo que elegiste (para validar)>",
  "titulo": "<apuesta en lenguaje natural y corto para mostrar al usuario. Ejemplos: 'Corners de Club Brugge: Over 3.5', 'Total de corners del partido: Over 7.5', 'Ambos equipos marcan: SI', 'HÃ¡ndicap asiatico Real Madrid -1.5', 'Total de puntos Lakers: Under 210.5'>",
  "selection": "<seleccion concreta: equipo A/B, SI/NO, over/under X.X, etc>",
  "odds": <cuota decimal estimada o null>,
  "confidence": "<ALTA|MEDIA|BAJA>",
  "rationale": "<1-2 frases del edge en espanol>",
  "stats": ["<dato corto 1 basado en los ultimos 5 partidos>", "<dato corto 2>", "<dato corto 3>", ...]
}
"""


# ============================================================
# GENERACION DE PICKS
# ============================================================


def _partidos_hoy():
    """Trae los partidos de hoy de todos los deportes del dashboard."""
    import sports

    ahora_local = datetime.now(timezone.utc).astimezone(sports.TZ_NIC)
    partidos = []
    for sport in DEPORTES_DASHBOARD:
        try:
            data = sports.get_sport_games(sport)
            for g in data.get("games", []):
                if g.get("state") == "post":
                    continue  # ya finalizados: no generar pick nuevo
                # Solo partidos de HOY (hora Nicaragua) o que esten en vivo:
                # el scoreboard ahora trae tambien manana y pasado manana.
                if g.get("state") != "in":
                    try:
                        fecha = datetime.fromisoformat(
                            str(g.get("date", "")).replace("Z", "+00:00")
                        ).astimezone(sports.TZ_NIC).date()
                    except (ValueError, TypeError):
                        fecha = ahora_local.date()
                    if fecha != ahora_local.date():
                        continue
                home = g.get("home") or {}
                away = g.get("away") or {}
                # Fallback tenis/MMA: 'teams' si no hay home/away
                if not home and not away:
                    equipos = g.get("teams") or []
                    away = equipos[0] if equipos else {}
                    home = equipos[1] if len(equipos) > 1 else {}
                home_name = home.get("name") or "?"
                away_name = away.get("name") or "?"
                partidos.append({
                    "sport": sport,
                    "label": data.get("label", sport),
                    "event_id": str(g.get("id", "")),
                    "event_name": f"{away_name} vs {home_name}",
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_logo": home.get("logo"),
                    "away_logo": away.get("logo"),
                    "date": g.get("date", ""),
                    "odds": g.get("odds"),
                    "league": g.get("league_code"),
                })
        except Exception as exc:
            print(f"[Dashboard] Error trayendo partidos {sport}: {exc}")
    return partidos


def _preguntar_ia(mensaje: str):
    """Pregunta a las DOS IAs. Primero 365AI (Groq); si falla, Demian (You.com).

    Devuelve (texto_respuesta, modelo_usado) o (None, None).
    """
    # IA 1: 365AI (Groq)
    try:
        from ai.ia36 import llamar_modelo, GROQ_API_KEY

        if GROQ_API_KEY:
            data, _ = llamar_modelo(
                [
                    {"role": "system", "content": PROMPT_PICKS},
                    {"role": "user", "content": mensaje},
                ],
                usar_tools=False,
                max_tokens=700,
            )
            content = ""
            choices = data.get("choices") or [{}]
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            if content:
                return content, "365AI"
    except Exception as exc:
        print(f"[Dashboard] 365AI fallo: {exc}")

    # IA 2: Demian (You.com)
    try:
        from engine.search_engine import SearchEngine

        respuesta = SearchEngine().ask_you(mensaje, system_prompt=PROMPT_PICKS)
        if respuesta:
            return respuesta, "Demian"
    except Exception as exc:
        print(f"[Dashboard] Demian fallo: {exc}")

    return None, None


def _parsear_pick_json(texto: str):
    """Extrae el JSON del pick de la respuesta de la IA (tolerante a markdown)."""
    if not texto:
        return None
    limpio = re.sub(r"```(?:json)?|```", "", texto).strip()
    match = re.search(r"\{.*\}", limpio, re.DOTALL)
    if not match:
        return None
    try:
        pick = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(pick, dict) or not pick.get("market") or not pick.get("selection"):
        return None
    return pick


def _market_norm(market: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (market or "").lower())


def _texto_rendimiento(hist_mercados: dict) -> str:
    """Bloque de rendimiento historico real de la IA para el prompt (Paso 2).

    Prioriza familias con buen %, avisa cuales estan vetadas por mal historial.
    """
    if not hist_mercados:
        return ""
    buenas = [
        (n, m) for n, m in hist_mercados.items()
        if m["efectividad"] >= REUTILIZAR_EFECTIVIDAD and m["resueltos"] >= REUTILIZAR_RESUELTOS
    ]
    malas = [
        (n, m) for n, m in hist_mercados.items()
        if m["efectividad"] < VETO_EFECTIVIDAD and m["resueltos"] >= VETO_RESUELTOS
    ]
    if not buenas and not malas:
        return ""
    lineas = ["\nRENDIMIENTO HISTORICO REAL de la IA (aciertos/resueltos por familia):"]
    if buenas:
        lineas += [
            f"- {n}: {m['efectividad']}% ({m['aciertos']}/{m['resueltos']}) <- familia confiable, puedes REPETIR este mercado entre partidos"
            for n, m in sorted(buenas, key=lambda x: -x[1]["efectividad"])[:8]
        ]
    if malas:
        lineas += [
            f"- {n}: {m['efectividad']}% ({m['aciertos']}/{m['resueltos']}) <- VETADA por mal historial: PROHIBIDO elegirla"
            for n, m in malas[:8]
        ]
    return "\n".join(lineas) + "\n"


def _familia_historica(fam_sin_sub: str, sub: str, hist_mercados: dict):
    return hist_mercados.get(fam_sin_sub + sub)


def _normalizar_ev(ev_raw: dict) -> dict | None:
    """Convierte un evento crudo de ESPN a {away: {name, score}, home: {...}}."""
    import sports

    comp0 = (ev_raw.get("competitions") or [{}])[0]
    salida = {}
    for c in comp0.get("competitors", []):
        lado = c.get("homeAway")
        if lado not in ("home", "away"):
            continue
        t = c.get("team", {})
        salida[lado] = {
            "name": t.get("displayName", "?"),
            "score": sports._parse_number(c.get("score")),
        }
    if "home" in salida and "away" in salida:
        return salida
    return None


def _recientes_con_marcador(sport: str, league, detail: dict, nombre: str) -> list:
    """Ultimos 10 partidos (con marcador) de un equipo.

    Misma estrategia de _analisis_previo: recent_games del detail + scoreboard
    de 45 dias + schedule de temporada hasta llegar a 10.
    """
    import sports

    equipo = next(
        (t for t in (detail.get("teams") or []) if t.get("name") == nombre), None
    )
    if not equipo:
        return []
    recientes = list(equipo.get("recent_games") or [])

    # Complemento 1: scoreboard de 45 dias
    if len(recientes) < 10:
        try:
            raws = sports.get_team_recent_events(
                sport, league, equipo.get("id"), limit=10
            )
            vistos = {
                (r.get("away", {}).get("name"), r.get("home", {}).get("name"))
                for r in recientes
            }
            for ev in reversed(raws):
                norm = _normalizar_ev(ev)
                if not norm:
                    continue
                clave = (norm["away"].get("name"), norm["home"].get("name"))
                if clave in vistos:
                    continue
                vistos.add(clave)
                recientes.append(norm)
                if len(recientes) >= 10:
                    break
        except Exception:
            pass

    # Complemento 2: schedule de temporada (futbol suele necesitarlo)
    if len(recientes) < 10:
        try:
            previos = sports.get_team_schedule_results(
                sport, league, equipo.get("id"), limit=10
            )
            vistos = {
                (r.get("away", {}).get("name"), r.get("home", {}).get("name"))
                for r in recientes
            }
            for ev in previos:
                clave = (ev.get("away", {}).get("name"), ev.get("home", {}).get("name"))
                if clave in vistos:
                    continue
                vistos.add(clave)
                recientes.append(ev)
                if len(recientes) >= 10:
                    break
        except Exception:
            pass

    return recientes[:10]


# ============================================================
# ESTADISTICAS DE PARTIDOS FINALIZADOS (corners, tiros, tarjetas...)
# ============================================================

# Cache: "sport:event_id" -> {"stats": {equipo_norm: {metrica: valor}},
# "halves": [h1, h2] | None, "date": "..."} o None. TTL 24h (un partido
# finalizado no cambia) y tope de memoria.
_STATS_CACHE = {}
_STATS_CACHE_TTL = 24 * 3600
_STATS_CACHE_MAX = 4000


def _stats_de_partido(sport: str, event_id, league=None) -> dict | None:
    """Estadisticas de equipo de un partido finalizado (ESPN /summary).

    Devuelve {"stats": {nombre_normalizado: {metrica_normalizada: valor}},
    "halves": [h1, h2] | None, "date": str} o None si no hay datos.
    """
    import sports

    if not event_id:
        return None
    clave = f"{sport}:{event_id}"
    ahora = time.time()
    hit = _STATS_CACHE.get(clave)
    if hit and ahora - hit["ts"] < _STATS_CACHE_TTL:
        return hit["data"]

    salida = None
    try:
        detail = sports.get_game_detail(sport, str(event_id))
        if not detail or detail.get("error"):
            detail = None
    except Exception:
        detail = None

    # Fallback: summary directo de la liga (no depende de get_sport_games,
    # que puede venir vacio y dejar a todo el futbol sin detalle)
    data = None
    if detail is None:
        try:
            path = sports.SPORTS[sport][0]
            url = (
                f"{sports.ESPN_BASE}/{path}/{league}/summary?event={event_id}"
                if (league and path.startswith("soccer"))
                else f"{sports.ESPN_BASE}/{path}/summary?event={event_id}"
            )
            data = sports._fetch_json(url)
        except Exception:
            data = None
        if data:
            try:
                header = (data.get("header", {}) or {}).get("competitions") or [{}]
                comp = header[0] if header else {}
                if ((comp.get("status") or {}).get("type") or {}).get("state") == "post":
                    teams_tmp = []
                    for c in comp.get("competitors", []):
                        teams_tmp.append({
                            "id": (c.get("team") or {}).get("id"),
                            "name": (c.get("team") or {}).get("displayName", "?"),
                            "homeAway": c.get("homeAway"),
                            "linescores": sports._linescores(c),
                        })
                    detail = {"teams": teams_tmp, "date": comp.get("date")}
                    stats_tmp = sports._parse_team_stats(data)
                    for t in detail["teams"]:
                        t["statistics"] = stats_tmp.get(str(t["id"]), [])
            except Exception:
                detail = None

    try:
        teams = (detail or {}).get("teams") or []
        if len(teams) >= 2:
            stats = {}
            for t in teams:
                nombre = _norm_texto(t.get("name"))
                if not nombre:
                    continue
                d = {}
                for s in t.get("statistics") or []:
                    crudo = s.get("label", s.get("displayValue"))
                    try:
                        d[_norm_texto(s.get("name"))] = float(crudo)
                    except (TypeError, ValueError):
                        continue
                if d:
                    stats[nombre] = d
            halves = None
            lines = {}
            try:
                for t in teams:
                    nombre = _norm_texto(t.get("name"))
                    ls = []
                    for x in t.get("linescores") or []:
                        try:
                            ls.append(float(x.get("value")))
                        except (TypeError, ValueError, AttributeError):
                            continue
                    if nombre and ls:
                        lines[nombre] = ls
            except Exception:
                lines = {}
            if stats:
                salida = {
                    "stats": stats,
                    "lines": lines,
                    "date": str(detail.get("date") or ""),
                }
    except Exception:
        salida = None

    if len(_STATS_CACHE) >= _STATS_CACHE_MAX:
        _STATS_CACHE.clear()
    _STATS_CACHE[clave] = {"ts": ahora, "data": salida}
    return salida


# Claves de estadistica NORMALIZADAS tal como llegan del detalle ESPN
# (STAT_TRANSLATIONS traduce: "Corners", "Tiros totales", "Tiros a puerta",
# "Tarjetas amarillas", "Faltas", "Fueras de juego"...). El lookup es por
# sufijo para tolerar prefijos de categoria ("x - corners").
_METRICAS_FUTBOL = {
    "woncorners": ("corners",),
    "totalshots": ("tiros totales", "totalshots"),
    "shotsontarget": ("tiros a puerta", "shotsontarget"),
    "yellowcards": ("tarjetas amarillas", "yellowcards"),
    "foulscommitted": ("faltas", "foulscommitted"),
    "offsides": ("fuera de juego", "offsides"),
}


def _valor_metrica(d: dict, metrica: str):
    """Valor numerico de la metrica canonica en el dict de stats, o None."""
    if not d:
        return None
    if metrica == "tarjetas":
        a, r = d.get("tarjetas amarillas"), d.get("tarjetas rojas")
        if a is None and "tarjetasamarillas" in d:
            a = d["tarjetasamarillas"]
        if r is None and "tarjetasrojas" in d:
            r = d["tarjetasrojas"]
        if a is not None or r is not None:
            return (a or 0) + (r or 0)
        return None
    for clave in _METRICAS_FUTBOL.get(metrica, (metrica,)):
        if clave in d:
            return d[clave]
    # tolerancia por sufijo (stats agrupadas traen prefijo "categoria - ")
    for k, v in d.items():
        if k.endswith(metrica):
            return v
    return None


def _metrica_equipo(stats_partido: dict, nombre_equipo: str, metrica: str):
    """Valor de la metrica canonica para el equipo en ese partido, o None."""
    d = (stats_partido or {}).get("stats") or {}
    equipo = d.get(_norm_texto(nombre_equipo))
    if not equipo:
        # tolerancia por nombre (ESPN usa displayName consistentes, pero por si acaso)
        for k, v in d.items():
            if k and (k in _norm_texto(nombre_equipo) or _norm_texto(nombre_equipo) in k):
                equipo = v
                break
    if not equipo:
        return None
    claves = _METRICAS_FUTBOL.get(metrica)
    if not claves:
        return None
    for c in claves:
        if c in equipo:
            return equipo[c]
    return None


def _recientes_con_id(sport: str, league, detail: dict, nombre: str) -> list:
    """Ultimos 10 del equipo; cada item trae 'id' de evento cuando existe."""
    recientes = _recientes_con_marcador(sport, league, detail, nombre)
    equipo = next(
        (t for t in (detail.get("teams") or []) if t.get("name") == nombre), None
    )
    if not equipo or not recientes:
        return recientes
    try:
        import sports
        raws = sports.get_team_recent_events(sport, league, equipo.get("id"), limit=10)
        # raws viene crudo; tomar ids por emparejamiento de fecha+rival
        ids_por_fecha = {}
        for ev in raws:
            comp0 = (ev.get("competitions") or [{}])[0]
            clave = str(comp0.get("date") or ev.get("date") or "")
            ids_por_fecha[clave] = ev.get("id")
        for r in recientes:
            if r.get("id"):
                continue
            r["id"] = ids_por_fecha.get(str(r.get("date") or ""))
    except Exception:
        pass
    return recientes


def _validar_stats_soccer(texto, sport, league, detail, home_name, away_name,
                          es_home, nombre_eq):
    """Frecuencia empirica de mercados de ESTADISTICAS en futbol.

    Logica: ¿en cuantos de sus ultimos partidos (temporada en curso) salio lo
    que el pick propone? Con estadisticas reales de ESPN por partido (corners,
    tiros, tiros a puerta, tarjetas, faltas, fuera de juego) y linescores por
    mitad para 'gana cualquier mitad / primera mitad'.

    Devuelve None (no aplica), ("nodata",) (sin datos: no bloquear) o
    (frecuencia, detalle).
    """
    import sports

    def _metrica():
        if "corner" in texto or "esquina" in texto:
            return "woncorners"
        if "a puerta" in texto:
            return "shotsontarget"
        if "tiros" in texto or "tiro" in texto:
            return "totalshots"
        if "amarilla" in texto:
            return "yellowcards"
        if "tarjeta" in texto:
            return "tarjetas"  # amarillas + rojas combinadas
        if "falta" in texto:
            return "foulscommitted"
        if "fuera de juego" in texto:
            return "offsides"
        return None

    metrica = _metrica()
    es_mitades = "mitad" in texto and "gana" in texto and metrica is None
    if metrica is None and not es_mitades:
        return None

    m_over = re.search(r"\b(?:over|mas de|minimo)\s*(\d+(?:\.\d+)?)", texto)
    m_under = re.search(r"\b(?:under|menos de|maximo)\s*(\d+(?:\.\d+)?)", texto)
    linea = None
    es_over = True
    if m_over or m_under:
        linea = float((m_over or m_under).group(1))
        es_over = bool(m_over)

    def _valor(d, m):
        return _valor_metrica(d, m)

    def _historial(nombre):
        """[{propio, rival, lines_propio, lines_rival}] de los ultimos juegos."""
        equipo = next(
            (t for t in (detail.get("teams") or []) if t.get("name") == nombre), None
        )
        if not equipo:
            return []
        eventos = []
        try:
            raws = sports.get_team_recent_events(
                sport, league, equipo.get("id"), limit=12
            ) or []
            for ev in raws:
                if ((ev.get("status") or {}).get("type") or {}).get("state") != "post":
                    continue
                if ev.get("id"):
                    eventos.append(ev.get("id"))
        except Exception:
            pass
        if len(eventos) < 6:
            try:
                for r in sports.get_team_schedule_results(
                    sport, league, equipo.get("id"), limit=12
                ) or []:
                    if r.get("id"):
                        eventos.append(r["id"])
            except Exception:
                pass
        clave_eq = _norm_texto(nombre)
        filas, vistos = [], set()
        for gid in eventos:
            gid = str(gid)
            if gid in vistos:
                continue
            vistos.add(gid)
            sp = _stats_de_partido(sport, gid, league)
            if not sp or not sp.get("stats"):
                continue
            prop = rival = None
            for nombre_sp, d in sp["stats"].items():
                if nombre_sp == clave_eq or nombre_sp in clave_eq or clave_eq in nombre_sp:
                    prop = d
                elif rival is None:
                    rival = d
            if prop is None:
                continue
            lines = sp.get("lines") or {}
            filas.append({
                "propio": prop,
                "rival": rival or {},
                "lines_propio": lines.get(clave_eq) or [],
                "lines_rival": next(
                    (v for k, v in lines.items() if k != clave_eq), []
                ),
            })
        return filas

    return _evaluar_mercado_stats(
        texto, metrica, es_mitades, linea, es_over, es_home, nombre_eq,
        home_name, away_name, _historial, _valor,
    )


def _evaluar_mercado_stats(texto, metrica, es_mitades, linea, es_over, es_home,
                           nombre_eq, home_name, away_name, _historial, _valor):
    """Ramas finales de _validar_stats_soccer (separada por tamano)."""
    # ---- Mitades: 'equipo gana cualquier mitad' / 'gana la primera mitad' ----
    if es_mitades:
        if es_home is None or not nombre_eq:
            return ("nodata",)
        propios = _historial(nombre_eq)
        con_dato = [f for f in propios if f["lines_propio"] and f["lines_rival"]]
        if len(con_dato) < 5:
            return ("nodata",)
        if "primera" in texto:
            pred = lambda f: f["lines_propio"][0] > f["lines_rival"][0]
        else:
            pred = lambda f: any(
                a > b for a, b in zip(f["lines_propio"], f["lines_rival"])
            )
        n = sum(1 for f in con_dato if pred(f))
        return (
            n / len(con_dato),
            f"salio en {n} de sus ultimos {len(con_dato)} partidos de {nombre_eq}",
        )

    def _cumple(valor):
        if valor is None or valor == linea:
            return False
        return valor > linea if es_over else valor < linea

    # Ambos equipos X cada uno: propio y rival cumplen en el mismo partido
    if "ambos" in texto and metrica:
        juegos = []
        for nombre in (home_name, away_name):
            for f in _historial(nombre):
                v1, v2 = _valor(f["propio"], metrica), _valor(f["rival"], metrica)
                if v1 is not None and v2 is not None:
                    juegos.append(v1 >= linea and v2 >= linea)
        if len(juegos) < 8:
            return ("nodata",)
        n = sum(1 for x in juegos if x)
        return n / len(juegos), (
            f"salio en {n} de los ultimos {len(juegos)} partidos (ambos equipos)"
        )

    # Total del partido (sin equipo en el titulo): suma propio+rival
    if es_home is None:
        juegos = []
        for nombre in (home_name, away_name):
            for f in _historial(nombre):
                v1, v2 = _valor(f["propio"], metrica), _valor(f["rival"], metrica)
                if v1 is not None and v2 is not None:
                    juegos.append(_cumple(v1 + v2))
        if len(juegos) < 10:
            return ("nodata",)
        n = sum(1 for x in juegos if x)
        return n / len(juegos), (
            f"salio en {n} de los ultimos {len(juegos)} partidos (ambos equipos)"
        )

    # Total de un equipo: la historia de ESE equipo
    propios = _historial(
        nombre_eq if nombre_eq else (home_name if es_home else away_name)
    )
    con_dato = [f for f in propios if _valor(f["propio"], metrica) is not None]
    if len(con_dato) < 5:
        return ("nodata",)
    n = sum(1 for f in con_dato if _cumple(_valor(f["propio"], metrica)))
    return (
        n / len(con_dato),
        f"salio en {n} de sus ultimos {len(con_dato)} partidos de {nombre_eq}",
    )


def _validacion_empirica(sport: str, event_id: str, home_name: str, away_name: str,
                         league, pick: dict):
    """Frecuencia empirica del outcome del pick en los ultimos 10 de cada equipo.

    Lee los marcadores reales de los ultimos 10 partidos del LOCAL y del
    VISITANTE, cuenta cuantas veces ocurrio lo que el pick propone
    (over/under X.5, ambos marcan, ganador, doble oportunidad, total de un
    equipo) y promedia ambos equipos.

    Devuelve (promedio, detalle) si valido; None si el mercado no es validable
    con marcadores o faltan datos (en cuyo caso NO se bloquea el pick).
    """
    import sports

    texto = _norm_texto(
        f"{pick.get('titulo') or ''} {pick.get('market') or ''} "
        f"{pick.get('selection') or ''}"
    )
    if not texto:
        return None

    try:
        detail = sports.get_game_detail(sport, event_id)
    except Exception:
        return None
    if not detail:
        return None

    def _marcadores(nombre):
        out = []
        for r in _recientes_con_marcador(sport, league, detail, nombre):
            a, h = r.get("away") or {}, r.get("home") or {}
            try:
                out.append((float(a.get("score")), float(h.get("score"))))
            except (TypeError, ValueError):
                continue
        return out

    rec_away = _marcadores(away_name)   # ultimos del visitante: (gf_visitante, gf_local)
    rec_home = _marcadores(home_name)   # ultimos del local
    if len(rec_away) < 6 or len(rec_home) < 6:
        return None  # pocos datos para juzgar

    def promedio(pred):
        """Promedio de la frecuencia del predicado en los 10 de cada equipo."""
        na = sum(1 for as_, hs in rec_away if pred(as_, hs))
        nh = sum(1 for as_, hs in rec_home if pred(as_, hs))
        detalle = f"{na}/{len(rec_away)} (visitante) y {nh}/{len(rec_home)} (local)"
        return (na / len(rec_away) + nh / len(rec_home)) / 2, detalle

    # equipo protagonista (para mercados de equipo: total propio, ML, 1X/2X)
    home_n, away_n = _norm_texto(home_name), _norm_texto(away_name)
    if home_n in texto and away_n not in texto:
        es_home, nombre_eq = True, home_name
    elif away_n in texto and home_n not in texto:
        es_home, nombre_eq = False, away_name
    else:
        es_home, nombre_eq = None, None

    # 0) Mercados de estadisticas (futbol): corners, tiros, tiros a puerta,
    # tarjetas, faltas, fuera de juego y mitades. Logica: ¿en cuantos de sus
    # ultimos partidos salio? CRITICO: va ANTES de la rama over/under de
    # marcadores (si no, un 'Over 9.5 corners' se evaluaria contra GOLES).
    if sport == "soccer":
        try:
            res_stats = _validar_stats_soccer(
                texto, sport, league, detail, home_name, away_name,
                es_home, nombre_eq,
            )
        except Exception:
            res_stats = None
        if res_stats is not None:
            if (
                isinstance(res_stats, tuple)
                and len(res_stats) == 2
                and res_stats[0] is not None
            ):
                return res_stats
            return None  # aplica pero sin datos: no bloquear el pick

    # 1) Ambos marcan
    if "ambos" in texto or "btts" in texto:
        return promedio(lambda a, h: a > 0 and h > 0)

    # 2) Over / Under con linea numerica
    m_over = re.search(r"\b(?:over|mas de)\s*(\d+(?:\.\d+)?)", texto)
    m_under = re.search(r"\b(?:under|menos de)\s*(\d+(?:\.\d+)?)", texto)
    if m_over or m_under:
        linea = float((m_over or m_under).group(1))
        es_over = bool(m_over)
        es_de_equipo = es_home is not None and (
            "de goles" in texto or "de puntos" in texto or "total de" in texto
        )
        if es_de_equipo:
            propios = rec_home if es_home else rec_away
            n = sum(
                1 for as_, hs in propios
                if ((as_ if es_home else hs) > linea) == es_over
                and (as_ if es_home else hs) != linea
            )
            freq = n / len(propios)
            return freq, f"{n}/{len(propios)} partidos de {nombre_eq}"
        pred = (lambda a, h: a + h > linea) if es_over else (lambda a, h: a + h < linea)
        return promedio(pred)

    # 3) ML / doble oportunidad (gana, no pierde, 1X/2X)
    doble = any(
        t in texto
        for t in ("no pierde", "1x", "2x", "doble oportunidad", "empate o", "o empate")
    )
    gana = any(t in texto for t in ("gana", "ganador", "victoria", "ml ", "moneyline"))
    if (gana or doble) and es_home is not None:
        propios = rec_home if es_home else rec_away
        if es_home:
            pred_win = lambda a, h: h > a
            pred_doble = lambda a, h: h >= a
        else:
            pred_win = lambda a, h: a > h
            pred_doble = lambda a, h: a >= h
        n = sum(1 for as_, hs in propios if (pred_doble if doble else pred_win)(as_, hs))
        freq = n / len(propios)
        return freq, f"{n}/{len(propios)} partidos de {nombre_eq}"

    return None  # mercado no validable con marcadores: no bloquear


def _analisis_previo(sport: str, event_id: str, home_name: str, away_name: str, league=None) -> str:
    """Analisis previo REAL (ESPN) para el prompt del pick.

    - Ultimos 10 partidos de cada equipo (resultado, marcador, rival, L/V).
    - Impacto local/visitante: record y promedios como local vs visitante.
    - H2H: ultimos enfrentamientos directos entre los dos equipos.
    Si el summary de ESPN trae pocos partidos recientes (pasa en futbol),
    se completa con el scoreboard de los ultimos 45 dias del equipo.
    """
    import sports

    def _normalizar(ev_raw: dict, nombre: str):
        """Convierte un evento crudo de ESPN al formato {away, home}."""
        comp0 = (ev_raw.get("competitions") or [{}])[0]
        salida = {}
        for c in comp0.get("competitors", []):
            lado = c.get("homeAway")
            if lado not in ("home", "away"):
                continue
            t = c.get("team", {})
            salida[lado] = {
                "name": t.get("displayName", "?"),
                "score": sports._parse_number(c.get("score")),
            }
        if "home" in salida and "away" in salida:
            return salida
        return None

    try:
        detail = sports.get_game_detail(sport, event_id)
    except Exception:
        detail = {}
    if not detail:
        return ""

    lineas = []
    for nombre in (away_name, home_name):
        equipo = next(
            (t for t in detail.get("teams", []) if t.get("name") == nombre), None
        )
        if not equipo:
            continue

        recientes = list(equipo.get("recent_games") or [])

        # Complemento 1: scoreboard de 45 dias para llegar a 10 partidos
        if len(recientes) < 10:
            try:
                raws = sports.get_team_recent_events(
                    sport, league, equipo.get("id"), limit=10
                )
                extra = []
                vistos = {
                    (r.get("away", {}).get("name"), r.get("home", {}).get("name"))
                    for r in recientes
                }
                for ev in reversed(raws):  # mas recientes primero
                    norm = _normalizar(ev, nombre)
                    if not norm:
                        continue
                    clave = (norm["away"].get("name"), norm["home"].get("name"))
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    extra.append(norm)
                    if len(recientes) + len(extra) >= 10:
                        break
                recientes.extend(extra)
            except Exception:
                pass

        # Complemento 2: schedule de temporada (futbol suele necesitarlo)
        if len(recientes) < 10:
            try:
                previos = sports.get_team_schedule_results(
                    sport, league, equipo.get("id"), limit=10
                )
                vistos = {
                    (
                        r.get("away", {}).get("name"),
                        r.get("home", {}).get("name"),
                        r.get("date", ""),
                    )
                    for r in recientes
                }
                ya = {
                    (r.get("away", {}).get("name"), r.get("home", {}).get("name"))
                    for r in recientes
                    if not r.get("date")
                }
                for ev in reversed(previos):
                    clave = (
                        ev.get("away", {}).get("name"),
                        ev.get("home", {}).get("name"),
                        ev.get("date", ""),
                    )
                    if clave in vistos:
                        continue
                    par = (clave[0], clave[1])
                    if par in ya:
                        continue
                    vistos.add(clave)
                    ya.add(par)
                    recientes.append(ev)
                    if len(recientes) >= 10:
                        break
                # ordenar por fecha si esta disponible (mas recientes ultimo)
                con_fecha = [r for r in recientes if r.get("date")]
                sin_fecha = [r for r in recientes if not r.get("date")]
                recientes = sin_fecha + con_fecha
            except Exception:
                pass

        recientes = recientes[:10]
        filas, g, e, p_ = [], 0, 0, 0
        lg = le = lp = lf = lc = 0  # split como local
        vg = ve = vp = vf = vc = 0  # split como visitante
        for r in recientes:
            a = r.get("away") or {}
            h = r.get("home") or {}
            es_local = (a.get("name") or "") != nombre
            propio = (h if es_local else a).get("score")
            rival_d = (a if es_local else h)
            rival_s = rival_d.get("score")
            rival = rival_d.get("name", "?")
            if propio is None or rival_s is None:
                continue
            try:
                pr, rs = float(propio), float(rival_s)
            except (TypeError, ValueError):
                continue
            if pr > rs:
                res, g = "G", g + 1
            elif pr < rs:
                res, p_ = "P", p_ + 1
            else:
                res, e = "E", e + 1
            if es_local:
                lg += res == "G"
                lp += res == "P"
                lf += pr
                lc += rs
            else:
                vg += res == "G"
                vp += res == "P"
                vf += pr
                vc += rs
            filas.append(f"{res} {pr:g}-{rs:g} {'vs' if es_local else '@'} {rival}")

        if not filas:
            continue
        n = len(filas)
        lineas.append(f"{nombre} - ultimos {n} partidos: {'; '.join(filas)}")
        lineas.append(
            f"{nombre} total: {g}G-{e}E-{p_}P en {n} | "
            f"COMO LOCAL: {lg}G-{le}E-{lp}P, {lf / n:.1f} gf, {lc / n:.1f} gc | "
            f"COMO VISITANTE: {vg}G-{ve}E-{vp}P, {vf / n:.1f} gf, {vc / n:.1f} gc"
        )

        # Rendimiento fisico: dias de descanso desde su ultimo partido
        fechas = [str(r.get("date"))[:10] for r in recientes if r.get("date")]
        if fechas:
            try:
                ult = max(fechas)
                dias = (
                    datetime.now(timezone.utc).date()
                    - datetime.strptime(ult, "%Y-%m-%d").date()
                ).days
                if 0 <= dias <= 45:
                    fisico = f"{dias} dia(s) de descanso desde su ultimo partido ({ult})"
                    if dias <= 2:
                        fisico += " <- POCO descanso (posible fatiga)"
                    elif dias >= 7:
                        fisico += " <- descanso largo (equipo fresco)"
                    lineas.append(f"{nombre} RENDIMIENTO FISICO: {fisico}")
            except Exception:
                pass

    # H2H: ultimos enfrentamientos directos (ESPN)
    h2h = (detail.get("head_to_head") or [])[:5]
    if h2h:
        filas = []
        for m in h2h:
            a, h = m.get("away") or {}, m.get("home") or {}
            fecha = str(m.get("date") or "")[:10]
            filas.append(
                f"{fecha}: {h.get('name', '?')} {h.get('score', '?')}-{a.get('score', '?')} {a.get('name', '?')}"
            )
        lineas.append("H2H (enfrentamientos directos): " + "; ".join(filas))

    return "\n".join(lineas)

def generar_picks_dia(max_partidos: int = 40) -> dict:
    """Genera picks automaticos para los partidos de hoy.

    Idempotente: salta partidos que ya tienen pick guardado hoy.
    Anti-repeticion relajada: no se repite un mercado entre partidos SALVO que
    su familia historica sea confiable (60%+ con muestras suficientes).
    Best-of-N: por partido se generan hasta 3 candidatos y se publica el de
    mejor validacion empirica (la diversidad estricta costaba aciertos).
    """
    partidos = _partidos_hoy()
    # 70/30: ligas grandes primero; las otras ligas/deportes solo hasta ~30%
    partidos.sort(key=lambda x: 0 if (x.get("league") or "") in LIGAS_GRANDES else 1)
    generados = 0
    omitidos = 0
    errores = 0
    rechazados_cuota = 0
    rechazados_calidad = 0
    rechazados_empiria = 0
    rechazados_historia = 0
    omitidos_liga = 0
    generados_grandes = 0
    generados_otros = 0

    mercados_usados = {_market_norm(r["market"]) for r in (db.list_picks_hoy() or [])}

    # Rendimiento historico por familia de mercado (track record real de la BD)
    hist = db.track_record(min_resueltos=REUTILIZAR_RESUELTOS) or {}
    hist_mercados = {
        m["nombre"]: m for m in (hist.get("mercados") or []) if m.get("resueltos")
    }
    familias_reutilizables = {
        n for n, m in hist_mercados.items()
        if m["efectividad"] >= REUTILIZAR_EFECTIVIDAD and m["resueltos"] >= REUTILIZAR_RESUELTOS
    }

    for p in partidos[:max_partidos]:
        if db.pick_existe(p["event_id"]):
            omitidos += 1
            continue

        # Cuota 70/30 entre ligas grandes y el resto
        es_grande = (p.get("league") or "") in LIGAS_GRANDES
        if not es_grande:
            limite_otros = max(
                2, round(CUOTA_OTRAS * (generados_grandes + generados_otros + 1))
            )
            if generados_otros + 1 > limite_otros:
                omitidos_liga += 1
                continue

        label, mercados = MERCADOS_POR_DEPORTE[p["sport"]]
        # Si ya se usaron todos los mercados Y no hay familias confiables
        # reutilizables, no tiene sentido seguir (se acabaria la variedad)
        if (
            not [m for m in mercados if _market_norm(m) not in mercados_usados]
            and not familias_reutilizables
        ):
            break

        mensaje = (
            f"Partido: {p['away_name']} (visitante) vs {p['home_name']} (local)\n"
            f"Deporte: {label}\nFecha/hora: {p['date']}\n"
        )

        # Cuotas reales: primero odds-api.io; si no hay, ESPN; si no, estima
        cuotas_texto = _cuotas_reales(p["sport"], p["home_name"], p["away_name"])
        if cuotas_texto:
            mensaje += cuotas_texto + "\n"
        else:
            cuotas = p.get("odds") or {}
            if cuotas.get("details") or cuotas.get("over_under"):
                mensaje += (
                    "Cuotas REALES de ESPN: "
                    f"linea={cuotas.get('details') or 'N/A'}, "
                    f"ML local={cuotas.get('home_odds') or 'N/A'}, "
                    f"ML visitante={cuotas.get('away_odds') or 'N/A'}, "
                    f"total de la casa={cuotas.get('over_under') or 'N/A'}. "
                    "BASATE en estas cuotas reales para calcular valor; la linea que "
                    "elijas debe respetar los minimos del catalogo.\n"
                )
            else:
                mensaje += (
                    "No hay cuotas reales disponibles para este partido: estima la "
                    "cuota y respetando los minimos del catalogo.\n"
                )

        # Analisis previo REAL (ESPN): ultimos 10 + local/visitante + H2H
        analisis = _analisis_previo(
            p["sport"], p["event_id"], p["home_name"], p["away_name"], p.get("league")
        )
        if analisis:
            mensaje += (
                f"\nANALISIS PREVIO REAL (ESPN) de {p['away_name']} vs {p['home_name']}:\n"
                f"{analisis}\n"
                "Basate en estas tendencias reales. En el campo 'stats' devuelve de 4 a 6 "
                "datos cortos (una linea cada uno) que cubran: forma reciente, el IMPACTO "
                "de jugar de local o visitante para la apuesta elegida, y el H2H si esta "
                "disponible. Ej: 'Gano 6 de sus ultimos 10', 'Como local: 4G-1P, promedio "
                "2.5 goles a favor', 'H2H: gano los ultimos 2 enfrentamientos'.\n"
            )

        # Rendimiento historico real como guia del prompt (Paso 2)
        mensaje_base = mensaje
        rend = _texto_rendimiento(hist_mercados)
        if rend:
            mensaje_base += rend

        # Best-of-N (Paso 4): hasta 3 candidatos por partido; gana el de mejor
        # validacion empirica. Cada intento excluye los mercados ya propuestos.
        exclusion = set(mercados_usados)
        mejor = None  # (prom|None, detalle|None, pick, market, modelo)
        for intento in range(BEST_OF_N):
            mensaje = mensaje_base + (
                f"\nElige UN solo mercado del catalogo de {label} (que NO sea uno de estos ya "
                f"usados hoy: {', '.join(sorted(exclusion)[:15]) or 'ninguno'}). "
                f"Recuerda: el campo 'market' es para validar contra el catalogo; el campo "
                f"'titulo' es la apuesta en lenguaje natural (ej: 'Corners de "
                f"{p['home_name']}: Over 3.5'). Devuelve el JSON del pick."
                f"\nIMPORTANTE: usa los nombres REALES de los equipos/jugadores "
                f"({p['home_name']} y {p['away_name']}); PROHIBIDO picks genericos tipo "
                f"'Jugador A', 'Local' o 'equipo B'. Si de verdad no tienes datos del "
                f"partido, responde {{\"error\": \"sin datos\"}} en vez de inventar."
                f"\nFECHA ACTUAL: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}. "
                f"El analisis y los datos deben ser de la temporada EN CURSO (incluye "
                f"el mes y el ano actual en tus busquedas y analisis, ej: 'equipo vs "
                f"equipo septiembre 2026'); descarta estadisticas de temporadas pasadas."
                f"\nIncluye el campo \"porque\": UNA linea corta (maximo 70 caracteres) "
                f"con la CONCLUSION que justifica el pick: la implicacion de la "
                f"tendencia para esta apuesta, con cifras reales. PROHIBIDO copiar "
                f"literal una linea de los datos/stats: debe ser la sintesis de la "
                f"tendencia (ej: 'Over 1.5 en 5 de los ultimos 6 H2H' o 'Vino over "
                f"2.5 en 4 de 5 de local'), no un dato suelto repetido."
            )

            texto, modelo = _preguntar_ia(mensaje)
            pick = _parsear_pick_json(texto)
            if not pick:
                errores += 1
                continue

            market = (pick.get("market") or "").strip()
            norma = _market_norm(market)
            titulo_pick = str(pick.get("titulo") or "")
            # Validar que el mercado pertenece al catalogo del deporte
            if norma not in {_market_norm(m) for m in mercados}:
                errores += 1
                continue

            # Historico de la familia de este candidato (Paso 2/3)
            fam_base = db._categoria_mercado(market, titulo_pick)
            fam_clave = fam_base + db._subcategoria_deporte(p["sport"], fam_base)
            hist_fam = hist_mercados.get(fam_clave)
            if (
                hist_fam
                and hist_fam["resueltos"] >= VETO_RESUELTOS
                and hist_fam["efectividad"] < VETO_EFECTIVIDAD
            ):
                rechazados_historia += 1
                continue

            # Anti-repeticion RELAJADA (Paso 3): repetir mercado entre partidos
            # solo se permite si la familia historica es confiable (60%+).
            if norma in exclusion and not (
                hist_fam
                and hist_fam["resueltos"] >= REUTILIZAR_RESUELTOS
                and hist_fam["efectividad"] >= REUTILIZAR_EFECTIVIDAD
            ):
                errores += 1
                continue

            # Rechazar picks con cuota demasiado baja (debe estar arriba de 1.20)
            try:
                odds_pick = float(pick.get("odds") or 0)
            except (TypeError, ValueError):
                odds_pick = 0
            if not odds_pick or odds_pick <= ODDS_MINIMA:
                rechazados_cuota += 1
                continue

            # Gate de calidad: nada de picks genericos o sin datos reales
            # (equipos '?', 'Jugador A', rationale 'sin datos...', sin cuota).
            rationale_txt = str(pick.get("rationale") or "") + " " + titulo_pick
            if (
                not _nombre_valido(p["home_name"])
                or not _nombre_valido(p["away_name"])
                or _texto_sin_datos(rationale_txt)
                or _texto_sin_datos(p["event_name"])
            ):
                rechazados_calidad += 1
                continue

            # Coherencia titulo vs seleccion (bug real: 'no pierde (1X)' con 2X)
            if _titulo_contradice(pick, p):
                rechazados_calidad += 1
                continue

            # La IA a veces admite en su propio texto que el pick es invalido
            # (ej: 'Alternativa invalida: Ohtani no juega este partido') y aun asi
            # lo emite: se rechaza directamente.
            if _pick_se_declara_invalido(pick):
                rechazados_calidad += 1
                continue

            # Props de jugador: el jugador nombrado debe pertenecer a uno de los
            # dos equipos del partido (bug real: Ohtani en White Sox vs Guardians).
            if not _jugador_valido_en_partido(pick, p):
                rechazados_calidad += 1
                continue

            # Mercados NO verificables con marcador (corners, tarjetas, props de
            # jugador...): no se generan (el resolver no puede validarlos).
            texto_pick = _norm_texto(f"{titulo_pick} {market}")
            if any(palabra in texto_pick for palabra in _MERCADOS_NO_VERIFICABLES):
                rechazados_calidad += 1
                continue

            exclusion.add(norma)

            # Validacion empirica del candidato: juez del best-of-N
            try:
                validacion = _validacion_empirica(
                    p["sport"], p["event_id"], p["home_name"], p["away_name"],
                    p.get("league"), pick,
                )
            except Exception:
                validacion = None
            if validacion is None:
                # No validable con marcadores: se acepta el primero que llegue
                if mejor is None:
                    mejor = (None, None, pick, market, modelo)
                break
            prom, detalle = validacion
            if mejor is None or prom > mejor[0]:
                mejor = (prom, detalle, pick, market, modelo)
            if prom >= PARADA_TEMPRANA_PROM:
                break  # candidato excelente: no gastar mas llamadas

        if mejor is None:
            continue
        prom, detalle, pick, market, modelo = mejor

        # Ningun candidato alcanzo la frecuencia minima: no se publica nada
        if prom is not None and prom < FRECUENCIA_MINIMA:
            rechazados_empiria += 1
            continue

        # Evidencia empirica como primer stat del pick
        if prom is not None:
            pick["stats"] = [
                f"Ultimos 10: ocurrio en {detalle} -> promedio {prom * 100:.0f}%"
            ] + [
                _limpiar_artefactos(str(s)) for s in (pick.get("stats") or [])[:4]
            ]

        creado = db.create_ai_pick(
            sport=p["sport"],
            sport_label=label,
            event_id=p["event_id"],
            event_name=p["event_name"],
            event_date=p["date"],
            market=market,
            selection=str(pick.get("selection", "")),
            porque=_porque_no_duplicado(
                _limpiar_artefactos(pick.get("porque", "")),
                pick.get("stats") or [],
                pick.get("rationale", ""),
            )[:160],
            odds=pick.get("odds"),
            confidence=pick.get("confidence", "MEDIA"),
            rationale=_limpiar_artefactos(pick.get("rationale", "")),
            model=modelo or "IA",
            home_name=p["home_name"],
            away_name=p["away_name"],
            home_logo=p["home_logo"],
            away_logo=p["away_logo"],
            titulo=_limpiar_artefactos(str(pick.get("titulo") or "").strip()) or str(pick.get("selection", "")),
            league=p.get("league"),
            stats=json.dumps(
                [_limpiar_artefactos(str(s)) for s in (pick.get("stats") or [])[:5]],
                ensure_ascii=False,
            ) if isinstance(pick.get("stats"), list) and pick.get("stats") else None,
        )
        if creado:
            generados += 1
            if es_grande:
                generados_grandes += 1
            else:
                generados_otros += 1
            mercados_usados.add(_market_norm(market))

    return {
        "partidos": len(partidos),
        "generados": generados,
        "de_ligas_grandes": generados_grandes,
        "de_otras_ligas": generados_otros,
        "omitidos_ya_con_pick": omitidos,
        "omitidos_otra_liga": omitidos_liga,
        "errores": errores,
        "rechazados_cuota": rechazados_cuota,
        "rechazados_calidad": rechazados_calidad,
        "rechazados_empiria": rechazados_empiria,
        "rechazados_historia": rechazados_historia,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# RESOLUCION DE PICKS (ACIERTO / FALLO)
# ============================================================


# Mercados que NO se pueden verificar con el resumen ESPN final (marcador +
# estadisticas de equipo). MLB props: el boxscore de ESPN no da ponches por
# pitcher de forma confiable al resolver.
_MERCADOS_NO_VERIFICABLES = (
    "ponche", "strikeout", "ponches",
    "doble resultado", "marcador exacto", "sets exactos",
)


def _resolucion_posible_con_marcador(pick: dict) -> bool:
    """False si el mercado requiere datos que el marcador final no da."""
    texto = f"{pick.get('market') or ''} {pick.get('titulo') or ''}".lower()
    return not any(palabra in texto for palabra in _MERCADOS_NO_VERIFICABLES)


def _resolver_pick_con_ia(pick: dict):
    """Pregunta a la IA si el pick fue ACIERTO o FALLO con el resultado final.

    DOBLE VERIFICACION: se pregunta 2 veces y ambas respuestas deben coincidir
    para marcar ACIERTO (la IA tiende a decir que todo fue acierto)."""
    import sports

    if not _resolucion_posible_con_marcador(pick):
        # Props/tarjetas/corners: el marcador final no alcanza para verificar
        return None

    try:
        detail = sports.get_game_detail(pick["sport"], pick["eventId"])
    except Exception:
        return None

    teams = detail.get("teams", [])
    if len(teams) < 2 or detail.get("state") != "post":
        return None

    marcador = " vs ".join(
        f"{t.get('name', '?')} {t.get('score', '-')}" for t in teams
    )

    # Estadisticas de equipo del partido finalizado (corners, tiros, tarjetas,
    # faltas...): necesarias para resolver esos mercados con el marcador.
    _INTERES = (
        "Corners", "Tiros totales", "Tiros a puerta", "Tarjetas amarillas",
        "Tarjetas rojas", "Faltas", "Fueras de juego", "Posesion %",
    )
    stats_lineas = []
    for t in teams:
        d = {}
        for s in t.get("statistics") or []:
            crudo = s.get("label", s.get("displayValue"))
            if crudo not in (None, ""):
                d[str(s.get("name"))] = crudo
        filas = [f"{k}={d[k]}" for k in _INTERES if d.get(k) not in (None, "")]
        if filas:
            stats_lineas.append(f"{t.get('name', '?')}: " + ", ".join(filas))

    # Jugadores destacados (para goleador/props): nombre + sus stats
    jugadores_lineas = []
    for j in (detail.get("key_players") or [])[:8]:
        st = ", ".join(
            f"{s.get('name')}: {s.get('value')}"
            for s in (j.get("stats") or [])[:4]
        )
        if st:
            jugadores_lineas.append(f"{j.get('name', '?')} - {st}")

    mensaje = (
        f"Pick realizado: mercado '{pick.get('market')}' - seleccion '{pick.get('selection')}'.\n"
        f"Partido: {pick.get('eventName')} (deporte {pick.get('sportLabel', '')}).\n"
        f"Resultado final: {marcador}.\n"
    )
    if stats_lineas:
        mensaje += "Estadisticas del partido:\n" + "\n".join(stats_lineas) + "\n"
    if jugadores_lineas:
        mensaje += "Jugadores destacados:\n" + "\n".join(jugadores_lineas) + "\n"
    mensaje += (
        f"NO adivines: si con el resultado final y estas estadisticas no puedes "
        f"determinarlo con certeza, responde INDETERMINADO. Props de jugador SOLO "
        f"se resuelven si el jugador aparece en la lista de destacados.\n\n"
        f"Con ese resultado y esas estadisticas, ¿el pick fue ACIERTO o FALLO?\n"
        f"Responde SOLO una palabra: ACIERTO o FALLO. Si el mercado no se puede "
        f"determinar con certeza, responde INDETERMINADO."
    )

    votos = []
    for _ in range(2):
        texto, _ = _preguntar_ia(mensaje)
        if not texto:
            continue
        upper = texto.upper()
        if "INDETERMINADO" in upper:
            return None
        if "ACIERTO" in upper and "FALLO" not in upper:
            votos.append("ACIERTO")
        elif "FALLO" in upper and "ACIERTO" not in upper:
            votos.append("FALLO")
    if len(votos) == 2 and votos[0] == votos[1]:
        return votos[0]
    return None


def backfill_picks_metadata() -> int:
    """Repara picks viejos sin nombres/logos de equipos usando los datos de hoy.

    Los picks generados antes de la correccion quedaron con home_name NULL y
    por eso el frontend mostraba '?'. Esto los actualiza con la info de ESPN.
    """
    viejos = db.picks_sin_equipo() or []
    if not viejos:
        return 0

    por_evento = {p["event_id"]: p for p in _partidos_hoy()}
    reparados = 0
    for pick in viejos:
        partido = por_evento.get(pick["eventId"])
        if not partido:
            continue
        if db.update_pick_metadata(
            pick["id"],
            partido["home_name"],
            partido["away_name"],
            partido["home_logo"],
            partido["away_logo"],
            partido.get("league"),
        ):
            reparados += 1
    return reparados


def _resolver_deterministico(pick: dict, detail: dict):
    """Resuelve el pick con reglas directas sobre el marcador final (sin IA).

    Cubre los mercados determinables: ganador, BTTS, over/under totales,
    doble oportunidad, sin empate, equipo total de goles. Devuelve
    ACIERTO/FALLO o None si el mercado no es determinable con el marcador.
    """
    teams = detail.get("teams") or []
    if len(teams) < 2:
        return None

    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
    try:
        hs = float(home.get("score"))
        as_ = float(away.get("score"))
    except (TypeError, ValueError):
        return None

    total = hs + as_
    market = (pick.get("market") or "").lower()
    sel = (pick.get("selection") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    texto = f"{market} {titulo} {sel}"

    home_name, away_name = (home.get("name") or "").lower(), (away.get("name") or "").lower()

    def _equipo_de(sel_txt: str):
        if home_name and home_name in sel_txt:
            return home
        if away_name and away_name in sel_txt:
            return away
        return None

    # ---- Ganador / ML / "X gana" ----
    if any(k in texto for k in ("ganador", " gana", "moneyline", " ml", "winner")) and \
       "primera mitad" not in texto and "prorroga" not in texto and "1er" not in texto:
        if hs == as_:
            return "FALLO"  # empate: solo gana quien apostó empate, que no damos
        ganador = home if hs > as_ else away
        elegido = _equipo_de(sel + " " + titulo)
        if elegido is None:
            return None
        return "ACIERTO" if elegido.get("name") == ganador.get("name") else "FALLO"

    # ---- Ambos equipos marcan / BTTS ----
    if "ambos" in texto and ("marcan" in texto or "anotan" in texto) or "btts" in texto:
        si = (hs > 0) and (as_ > 0)
        eligio_si = not sel.strip().startswith("no")
        return "ACIERTO" if si == eligio_si else "FALLO"

    # ---- Apuesta sin empate (Draw No Bet) ----
    if "sin empate" in texto:
        if hs == as_:
            return "FALLO"
        elegido = _equipo_de(sel + " " + titulo)
        if elegido is None:
            return None
        ganador = home if hs > as_ else away
        return "ACIERTO" if elegido.get("name") == ganador.get("name") else "FALLO"

    # ---- Doble oportunidad ----
    if "doble oportunidad" in texto or "doble oport" in texto:
        codigo = sel.replace(" ", "").upper()
        # Normalizar variantes del catalogo (2X == X2, 21 == 12)
        codigo = {"2X": "X2", "21": "12"}.get(codigo, codigo).lower()
        if hs == as_:
            return "ACIERTO" if codigo in ("1x", "x2") else "FALLO"
        gana_local = hs > as_
        if codigo == "1x":
            return "ACIERTO" if gana_local else "FALLO"
        if codigo == "x2":
            return "ACIERTO" if not gana_local else "FALLO"
        if codigo == "12":
            return "ACIERTO"
        return None

    # ---- Over/Under de goles/puntos/corners (total o de un equipo) ----
    match = re.search(
        r"(over|m[áa]s de|under|menos de)\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)", texto
    )
    if match:
        tipo = match.group(1).lower()
        try:
            linea = float(match.group(2).replace(",", "."))
        except ValueError:
            return None
        es_over = tipo.startswith(("over", "m"))
        # Si es un total de un equipo concreto, usar su marcador
        if ("equipo" in texto or "total de goles" in texto or "totales" in texto) and \
           "prorroga" not in texto:
            equipo = _equipo_de(sel + " " + titulo)
            if equipo is not None:
                try:
                    valor = float(equipo.get("score"))
                except (TypeError, ValueError):
                    return None
                return ("ACIERTO" if valor > linea else "FALLO") if es_over else \
                       ("ACIERTO" if valor < linea else "FALLO")
        return ("ACIERTO" if total > linea else "FALLO") if es_over else \
               ("ACIERTO" if total < linea else "FALLO")

    return None


def _detalle_resolucion(pick: dict):
    """Obtiene el detalle del partido para resolver el pick.

    A diferencia de get_game_detail (que para soccer solo busca en el
    scoreboard de hoy), usa la liga guardada en el pick para consultar el
    summary de ESPN aunque el partido sea de dias anteriores.
    """
    import sports

    sport = pick["sport"]
    eid = pick["eventId"]

    # Futbol: summary con la liga guardada
    if sport == "soccer":
        league = pick.get("league")
        if not league:
            return sports.get_game_detail(sport, eid)
        try:
            import requests as http_requests

            from sports import ESPN_BASE, _parse_number

            url = f"{ESPN_BASE}/soccer/{league}/summary?event={eid}"
            data = http_requests.get(url, timeout=20).json()
            header = data.get("header") or {}
            comps = (header.get("competitions") or [{}])[0]
            state = ((comps.get("status") or {}).get("type") or {}).get("state")
            teams = []
            for c in comps.get("competitors", []):
                teams.append({
                    "name": (c.get("team") or {}).get("displayName", "?"),
                    "score": _parse_number(c.get("score")),
                    "homeAway": c.get("homeAway"),
                })
            return {"state": state, "teams": teams}
        except Exception:
            return sports.get_game_detail(sport, eid)

    # Resto de deportes: get_game_detail funciona directo
    return sports.get_game_detail(sport, eid)


def _detalle_desde_oddsapi(pick: dict):
    """Fallback: marcador final desde odds-api.io (eventos settled incluyen scores).

    Permite resolver picks de partidos que ya no aparecen en ESPN.
    """
    slug = ODDS_SPORT_SLUGS.get(pick["sport"])
    if not slug:
        return None

    eventos = _odds_eventos(slug)

    def _coincide(a: str, b: str) -> bool:
        a, b = _norm_texto(a), _norm_texto(b)
        return bool(a) and bool(b) and (a == b or a in b or b in a)

    def _parse_fecha(txt) -> "datetime":
        t = str(txt).replace("Z", "").replace("T", " ").strip()
        t = re.sub(r"\+00:00$", "", t)
        return datetime.fromisoformat(t)

    for e in eventos:
        if e.get("status") != "settled":
            continue
        # Debe ser EL MISMO partido: en las series (MLB/NBA) los mismos
        # equipos juegan varios dias seguidos y el matching solo por nombre
        # llego a resolver picks de HOY con el marcador de AYER.
        try:
            ev_odds = _parse_fecha(e.get("date"))
            ev_pick = _parse_fecha(pick.get("eventDate"))
        except (ValueError, TypeError):
            continue
        if abs((ev_odds - ev_pick).total_seconds()) > 12 * 3600:
            continue
        scores = e.get("scores") or {}
        if scores.get("home") is None or scores.get("away") is None:
            continue
        if _coincide(e.get("home", ""), pick.get("homeName") or "") and _coincide(
            e.get("away", ""), pick.get("awayName") or ""
        ):
            return {
                "state": "post",
                "teams": [
                    {"name": e["home"], "score": scores["home"], "homeAway": "home"},
                    {"name": e["away"], "score": scores["away"], "homeAway": "away"},
                ],
            }
    return None


def resolver_picks_finalizados() -> dict:
    """Resuelve los picks pendientes cuyos partidos ya terminaron.

    Primero intenta resolucion deterministica con el marcador final; si el
    mercado no es determinable, pregunta a la IA.
    """
    import sports

    pendientes = db.list_picks_pendientes() or []
    resueltos = 0
    odds_hoy = None  # snapshot de cuotas de los partidos de hoy (lazy)
    for pick in pendientes:
        # Expirar picks antiguos que ya no se pueden resolver (sin liga,
        # evento fuera de ESPN, etc.) para no bloquear la cola de pendientes
        try:
            creado = datetime.fromisoformat(pick.get("createdAt") or "")
            horas = (datetime.now(timezone.utc).replace(tzinfo=None) - creado).total_seconds() / 3600
        except (ValueError, TypeError):
            horas = 0
        if horas > 24:
            db.update_pick_result(pick["id"], "ANULADO")
            continue

        # Expirar pendientes cuyo EVENTO ya paso hace mas de 3h y sigue sin
        # resolverse (eventos fantasma de ESPN, tenis sin datos, etc.)
        try:
            ev = datetime.fromisoformat(str(pick.get("eventDate") or "").replace("Z", "+00:00"))
            horas_evento = (datetime.now(timezone.utc) - ev).total_seconds() / 3600
        except (ValueError, TypeError):
            horas_evento = 0
        if horas_evento > 3:
            db.update_pick_result(pick["id"], "ANULADO")
            continue

        # Cuota de CIERRE (para medir CLV): snapshot de la linea del partido
        # al arrancar (o ya en juego), guardado UNA sola vez por pick.
        if not (pick.get("closingOdds") or pick.get("closing_odds")):
            try:
                if horas_evento >= -1:
                    if odds_hoy is None:
                        odds_hoy = {
                            str(p.get("event_id")): p.get("odds")
                            for p in (_partidos_hoy() or [])
                        }
                    crudo = odds_hoy.get(str(pick.get("eventId"))) or {}
                    snap = {
                        k: crudo.get(k)
                        for k in ("details", "overUnder", "home_odds", "away_odds", "provider")
                        if crudo.get(k) is not None
                    }
                    if snap:
                        db.set_closing_odds(pick["id"], json.dumps(snap, ensure_ascii=False))
            except Exception:
                pass

        try:
            detail = _detalle_resolucion(pick)
            # Fallback: marcador final desde odds-api.io si ESPN no lo tiene
            if not detail or detail.get("state") not in ("post", "in"):
                detail = _detalle_desde_oddsapi(pick) or detail
        except Exception:
            continue

        if detail.get("state") != "post" or len(detail.get("teams") or []) < 2:
            continue

        # 1) Reglas directas con el marcador
        resultado = _resolver_deterministico(pick, detail)
        # 2) IA solo si el mercado no es determinable con el marcador
        if resultado is None:
            resultado = _resolver_pick_con_ia(pick)
        if resultado:
            db.update_pick_result(pick["id"], resultado)
            resueltos += 1
    return {"pendientes": len(pendientes), "resueltos": resueltos}


# ============================================================
# RESUMEN PARA EL DASHBOARD
# ============================================================


def aciertos_visibles() -> list:
    """Aciertos que se muestran en el dashboard.

    - Los de HOY siempre.
    - Los de AYER solo hasta las 23:00 hora Nicaragua (con etiqueta 'Ayer').
    - Nunca muestra picks con cuota <= ODDS_MINIMA.
    """
    aciertos = db.list_picks_aciertos_hoy_ayer() or []
    ahora_local = _hora_nicaragua()
    mostrar_ayer = ahora_local.hour < HORA_CORTE_ACERTADOS
    visibles = []
    for p in aciertos:
        if not _pick_calidad_ok(p):
            continue
        es_ayer = (p.get("pickDate") or "") < ahora_local.date().isoformat()
        if es_ayer and not mostrar_ayer:
            continue
        visibles.append(p)
    return visibles


def _evento_vigente(pick: dict) -> bool:
    """True si el evento del pick es de HOY (Nicaragua) o futuro.

    Los pendientes cuyo partido ya paso (ej. '09 sept') salen del dashboard:
    quedan en la BD para el historico, pero no se muestran como 'del dia'.
    """
    fecha_raw = pick.get("eventDate")
    if not fecha_raw:
        return True  # sin fecha no podemos descartarlo
    try:
        dt = datetime.fromisoformat(str(fecha_raw).replace("Z", "+00:00"))
        fecha_evento = dt.astimezone(TZ_NICARAGUA).date()
    except (ValueError, TypeError):
        return True
    return fecha_evento >= _hora_nicaragua().date()


def revisar_picks_hoy(corregir: bool = False) -> dict:
    """Re-verifica TODOS los picks de HOY contra las estadisticas reales.

    - ACIERTO/FALLO: recalcula el resultado del mercado con el marcador/stats
      del partido (ESPN; fallback odds-api.io) y marca SOSPECHOSO si no
      coincide con el resultado guardado.
    - PENDIENTE: si el partido esta EN VIVO reporta el marcador actual y si el
      pick va ganando o perdiendo; si ya termino y sigue pendiente, alerta.
    - corregir=True: corrige automaticamente los resultados mal guardados
      (partido post: aplica el recalculado; en vivo: reinicia a PENDIENTE
      para que el resolver lo procese al terminar).
    """
    picks = db.list_picks_hoy() or []
    revisados, sospechosos, en_vivo = 0, [], []

    for pick in picks:
        estado = pick.get("result")
        try:
            detail = _detalle_resolucion(pick)
            if not detail or detail.get("state") not in ("post", "in"):
                detail = _detalle_desde_oddsapi(pick) or detail
        except Exception:
            continue
        if not detail:
            continue
        state = detail.get("state")
        if not state:
            continue

        revisados += 1
        marcador = " | ".join(
            f"{(t.get('name') or '?')}: {t.get('score')}"
            for t in (detail.get("teams") or [])
        )
        item = {
            "id": pick.get("id"),
            "titulo": pick.get("titulo"),
            "market": pick.get("market"),
            "evento": pick.get("eventName"),
            "estado_guardado": estado,
            "estado_partido": state,
            "marcador": marcador,
            "resultado_recalculado": None,
        }

        try:
            recalculado = _resolver_deterministico(pick, detail)
        except Exception:
            recalculado = None
        item["resultado_recalculado"] = recalculado

        if estado == "PENDIENTE":
            if state == "in":
                item["va_ganando"] = (
                    f" provisional: {recalculado}" if recalculado else " mercado no decidible con marcador"
                )
                en_vivo.append(item)
            elif state == "post" and recalculado:
                sospechosos.append({
                    **item,
                    "nota": "partido TERMINADO pero el pick sigue PENDIENTE (resolver no lo proceso)",
                })
        elif recalculado and recalculado != estado:
            item["nota"] = f"guardado como {estado} pero el marcador real indica {recalculado}"
            if corregir and recalculado in ("ACIERTO", "FALLO"):
                if state == "post":
                    db.update_pick_result(pick["id"], recalculado)
                    item["corregido"] = True
                elif state == "in":
                    # En vivo con resultado guardado equivocado: reiniciar a
                    # PENDIENTE para que el resolver lo procese al terminar.
                    db.update_pick_result(pick["id"], "PENDIENTE")
                    item["reiniciado"] = True
            sospechosos.append(item)

    return {"revisados": revisados, "sospechosos": sospechosos, "en_vivo": en_vivo}


def resumen_dashboard(username: str) -> dict:
    """Bienvenida + stats del dashboard.

    - pronosticos_del_dia: SOLO los pendientes de hoy (los acertados se van
      moviendo a la seccion de acertados; los fallados nunca se muestran).
      Nunca se muestran picks con cuota <= ODDS_MINIMA (1.20).
    - acertados: picks de HOY y de AYER con resultado ACIERTO (los de ayer solo
      hasta las 23:00 Nicaragua). Cada pick trae fechaLabel ('Hoy HH:MM' /
      'Ayer HH:MM') en hora Nicaragua.
    - efectividad_hoy: aciertos / resueltos de HOY (coherente con los KPIs).
    - historico: acumulado de todos los dias (se muestra aparte). Se limpia
      automaticamente cuando se borran todos los picks (/dashboard/reset).
    """
    picks = db.list_picks_hoy() or []
    aciertos_hoy = [p for p in picks if p.get("result") == "ACIERTO"]
    fallados = [p for p in picks if p.get("result") == "FALLO"]
    pendientes = [
        p for p in picks
        if p.get("result") == "PENDIENTE"
        and _pick_calidad_ok(p)
        and _evento_vigente(p)
    ]

    # Aciertos visibles: hoy + ayer (hasta 23:00 Nicaragua), sin cuotas bajas
    aciertos_visibles_lista = aciertos_visibles()
    ids_hoy = {p["id"] for p in aciertos_hoy}
    aciertos_de_ayer = [p for p in aciertos_visibles_lista if p["id"] not in ids_hoy]

    historial = db.count_aciertos_historico() or {}

    resueltos_hoy = len(aciertos_hoy) + len(fallados)
    efectividad_hoy = (
        round(len(aciertos_hoy) / resueltos_hoy * 100) if resueltos_hoy else None
    )

    por_deporte = {}
    for p in pendientes:
        key = p.get("sport_label") or p.get("sport")
        por_deporte[key] = por_deporte.get(key, 0) + 1

    return {
        "welcome": f"Bienvenido, {username}",
        "stats": {
            "pronosticos_del_dia": len(pendientes),
            "pronosticos_acertados_por_la_ia": len(aciertos_visibles_lista),
            "aciertos_hoy": len(aciertos_hoy),
            "aciertos_ayer": len(aciertos_de_ayer),
            "fallados_hoy": len(fallados),
            "resueltos_hoy": resueltos_hoy,
            "efectividad_hoy": efectividad_hoy,
            "por_deporte": por_deporte,
            "historico_aciertos": historial.get("aciertos", 0),
            "historico_resueltos": historial.get("resueltos", 0),
        },
        "pronosticos_del_dia": pendientes,
        "pronosticos_acertados": aciertos_visibles_lista,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# SCHEDULER AUTOMATICO
# ============================================================

INTERVALO_SEGUNDOS = max(300, int(os.getenv("DASHBOARD_PICKS_INTERVAL", "1800")))
_generando = threading.Lock()


def _ciclo():
    with _generando:
        try:
            reparados = backfill_picks_metadata()
            if reparados:
                print(f"[Dashboard] Picks reparados con equipos/logos: {reparados}", flush=True)
        except Exception:
            print("[Dashboard] Error en backfill de metadata:\n" + traceback.format_exc(), flush=True)
        try:
            stats = generar_picks_dia()
            print(f"[Dashboard] Picks automaticos: {stats}", flush=True)
        except Exception:
            print("[Dashboard] Error en ciclo de picks:\n" + traceback.format_exc(), flush=True)
        try:
            res = resolver_picks_finalizados()
            if res.get("resueltos"):
                print(f"[Dashboard] Picks resueltos: {res}", flush=True)
        except Exception:
            print("[Dashboard] Error resolviendo picks:\n" + traceback.format_exc(), flush=True)
        try:
            # ALERTA de picks estancados: partido terminado/en vivo con el
            # pick sin resolver (fallo silencioso del resolutor = datos
            # corruptos en el track record).
            auditoria = revisar_picks_hoy()
            sospechosos = auditoria.get("sospechosos") or []
            en_vivo = auditoria.get("en_vivo") or []
            if sospechosos:
                detalle = "; ".join(
                    f"{s.get('eventName')}: {s.get('nota') or s.get('estado') or 'sospechoso'}"
                    for s in sospechosos[:5]
                )
                print(
                    f"[Dashboard] ALERTA: {len(sospechosos)} picks sospechosos -> {detalle}",
                    flush=True,
                )
                try:
                    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_log.txt")
                    with open(ruta, "a", encoding="utf-8") as fh:
                        fh.write(
                            f"{time.strftime('%Y-%m-%d %H:%M:%S')} | ALERTA | "
                            f"{len(sospechosos)} picks sospechosos: {detalle}\n"
                        )
                except Exception:
                    pass
            if en_vivo:
                print(f"[Dashboard] Picks en vivo: {len(en_vivo)}", flush=True)
        except Exception:
            print("[Dashboard] Error auditando picks:\n" + traceback.format_exc(), flush=True)


def _loop():
    time.sleep(20)  # dejar arrancar la app primero
    while True:
        _ciclo()
        time.sleep(INTERVALO_SEGUNDOS)


def iniciar_scheduler():
    """Arranca el hilo daemon que genera y resuelve picks automaticamente."""
    hilo = threading.Thread(target=_loop, daemon=True, name="dashboard-picks")
    hilo.start()
    print("[Dashboard] Scheduler de picks automaticos iniciado", flush=True)
