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
)

_NOMBRES_INVALIDOS = {"", "?", "n/a", "na", "jugador a", "jugador b",
                      "equipo a", "equipo b", "local", "visitante",
                      "team a", "team b", "home", "away", "jugador 1", "jugador 2"}


def _nombre_valido(nombre) -> bool:
    return bool(nombre) and str(nombre).strip().lower() not in _NOMBRES_INVALIDOS


def _texto_sin_datos(texto) -> bool:
    t = (texto or "").lower()
    return any(f in t for f in _FRASES_SIN_DATOS)


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
# Plan free: solo 2 bookmakers recreativos permitidos
ODDS_BOOKMAKERS = "1xbet,Stake"
# Mapeo de nuestros deportes a los slugs de odds-api.io
ODDS_SPORT_SLUGS = {
    "soccer": "football",
    "nba": "basketball",
    "mlb": "baseball",
    "tennis": "tennis",
}

_odds_cache = {}  # clave -> (timestamp, data)
_ODDS_CACHE_TTL = 600  # 10 min (respeta el rate limit de 100 req/hora)
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
    """Lista de eventos de un deporte, con cache de 10 min."""
    ahora = time.time()
    cached = _odds_cache.get(f"events:{sport_slug}")
    if cached and ahora - cached[0] < _ODDS_CACHE_TTL:
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


def _cuotas_reales(sport: str, home_name: str, away_name: str) -> str:
    """Devuelve un texto con las cuotas reales (odds-api.io) del partido.

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
        return ""

    mercados = _odds_evento(evento.get("id"))
    if not mercados:
        return ""

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
3. NUNCA repitas el mismo mercado en los diferentes o mismos partidos.
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


def _ultimos5(sport: str, event_id: str, home_name: str, away_name: str) -> dict:
    """Ultimos 5 partidos reales (ESPN) de cada equipo del evento.

    Devuelve {"away": ["G 2-1 vs X", ...], "home": [...]} con resultado
    (G ganÃ³, P perdiÃ³, E empate), marcador y rival.
    """
    import sports

    def _resultado(mi, mi_score, rival, rival_score):
        if mi_score is None or rival_score is None:
            return f"vs {rival} ({r.get('status', '')})"
        try:
            mi_s, ri_s = float(mi_score), float(rival_score)
        except (TypeError, ValueError):
            return f"vs {rival} ({r.get('status', '')})"
        if mi_s > ri_s:
            res = "G"
        elif mi_s < ri_s:
            res = "P"
        else:
            res = "E"
        return f"{res} {mi_s:g}-{ri_s:g} vs {rival}"

    salida = {"home": [], "away": []}
    try:
        detail = sports.get_game_detail(sport, event_id)
    except Exception:
        return salida

    for lado, nombre in (("home", home_name), ("away", away_name)):
        equipo = next(
            (t for t in detail.get("teams", []) if t.get("name") == nombre), None
        )
        if not equipo:
            continue
        for r in (equipo.get("recent_games") or [])[:5]:
            a = r.get("away") or {}
            h = r.get("home") or {}
            if (a.get("name") or "") == nombre:
                fila = _resultado(nombre, a.get("score"), h.get("name", "?"), h.get("score"))
            elif (h.get("name") or "") == nombre:
                fila = _resultado(nombre, h.get("score"), a.get("name", "?"), a.get("score"))
            else:
                continue
            salida[lado].append(fila)
    return salida


def generar_picks_dia(max_partidos: int = 40) -> dict:
    """Genera picks automaticos para los partidos de hoy.

    Idempotente: salta partidos que ya tienen pick guardado hoy.
    Nunca repite el mismo mercado (normalizado) en toda la jornada.
    """
    partidos = _partidos_hoy()
    generados = 0
    omitidos = 0
    errores = 0
    rechazados_cuota = 0
    rechazados_calidad = 0

    mercados_usados = {_market_norm(r["market"]) for r in (db.list_picks_hoy() or [])}

    for p in partidos[:max_partidos]:
        if db.pick_existe(p["event_id"]):
            omitidos += 1
            continue

        label, mercados = MERCADOS_POR_DEPORTE[p["sport"]]
        if not [m for m in mercados if _market_norm(m) not in mercados_usados]:
            break  # ya se usaron todos los mercados del catalogo hoy

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

        # Ultimos 5 partidos reales de cada equipo (ESPN) para dar contexto
        recientes = _ultimos5(p["sport"], p["event_id"], p["home_name"], p["away_name"])
        if recientes.get("away") or recientes.get("home"):
            mensaje += (
                f"\nULTIMOS 5 PARTIDOS REALES (ESPN) de {p['away_name']}: "
                f"{'; '.join(recientes.get('away') or ['sin datos'])}\n"
                f"ULTIMOS 5 PARTIDOS REALES (ESPN) de {p['home_name']}: "
                f"{'; '.join(recientes.get('home') or ['sin datos'])}\n"
                "Analiza esas tendencias y en el campo 'stats' devuelve de 3 a 5 "
                "datos cortos (una linea cada uno) que sustenten ESTE pick, basados "
                "UNICAMENTE en esos partidos reales. Ej: 'Gano 4 de sus ultimos 5', "
                "'Marco en los ultimos 3 partidos', '2 de 3 con BTTS'.\n"
            )

        mensaje += (
            f"\nElige UN solo mercado del catalogo de {label} (que NO sea uno de estos ya "
            f"usados hoy: {', '.join(list(mercados_usados)[:15]) or 'ninguno'}). "
            f"Recuerda: el campo 'market' es para validar contra el catalogo; el campo "
            f"'titulo' es la apuesta en lenguaje natural (ej: 'Corners de "
            f"{p['home_name']}: Over 3.5'). Devuelve el JSON del pick."
            f"\nIMPORTANTE: usa los nombres REALES de los equipos/jugadores "
            f"({p['home_name']} y {p['away_name']}); PROHIBIDO picks genericos tipo "
            f"'Jugador A', 'Local' o 'equipo B'. Si de verdad no tienes datos del "
            f"partido, responde {{\"error\": \"sin datos\"}} en vez de inventar."
        )

        texto, modelo = _preguntar_ia(mensaje)
        pick = _parsear_pick_json(texto)

        if not pick:
            errores += 1
            continue

        market = (pick.get("market") or "").strip()
        # Validar que el mercado pertenece al catalogo del deporte y no repite
        if _market_norm(market) not in {_market_norm(m) for m in mercados}:
            errores += 1
            continue
        if _market_norm(market) in mercados_usados:
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
        rationale_txt = str(pick.get("rationale") or "") + " " + str(pick.get("titulo") or "")
        if (
            not _nombre_valido(p["home_name"])
            or not _nombre_valido(p["away_name"])
            or _texto_sin_datos(rationale_txt)
            or _texto_sin_datos(p["event_name"])
        ):
            rechazados_calidad += 1
            continue

        creado = db.create_ai_pick(
            sport=p["sport"],
            sport_label=label,
            event_id=p["event_id"],
            event_name=p["event_name"],
            event_date=p["date"],
            market=market,
            selection=str(pick.get("selection", "")),
            odds=pick.get("odds"),
            confidence=pick.get("confidence", "MEDIA"),
            rationale=pick.get("rationale", ""),
            model=modelo or "IA",
            home_name=p["home_name"],
            away_name=p["away_name"],
            home_logo=p["home_logo"],
            away_logo=p["away_logo"],
            titulo=str(pick.get("titulo") or "").strip() or str(pick.get("selection", "")),
            league=p.get("league"),
            stats=json.dumps(
                [str(s) for s in (pick.get("stats") or [])[:5]],
                ensure_ascii=False,
            ) if isinstance(pick.get("stats"), list) and pick.get("stats") else None,
        )
        if creado:
            generados += 1
            mercados_usados.add(_market_norm(market))

    return {
        "partidos": len(partidos),
        "generados": generados,
        "omitidos_ya_con_pick": omitidos,
        "errores": errores,
        "rechazados_cuota": rechazados_cuota,
        "rechazados_calidad": rechazados_calidad,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# RESOLUCION DE PICKS (ACIERTO / FALLO)
# ============================================================


def _resolver_pick_con_ia(pick: dict):
    """Pregunta a la IA si el pick fue ACIERTO o FALLO con el resultado final."""
    import sports

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
    mensaje = (
        f"Pick realizado: mercado '{pick.get('market')}' - seleccion '{pick.get('selection')}'.\n"
        f"Partido: {pick.get('eventName')} (deporte {pick.get('sportLabel', '')}).\n"
        f"Resultado final: {marcador}.\n\n"
        f"Con ese resultado final, Â¿el pick fue ACIERTO o FALLO?\n"
        f"Responde SOLO una palabra: ACIERTO o FALLO. Si el mercado no se puede "
        f"determinar con ese marcador, responde INDETERMINADO."
    )

    texto, _ = _preguntar_ia(mensaje)
    if not texto:
        return None
    upper = texto.upper()
    if "INDETERMINADO" in upper:
        return None
    if "ACIERTO" in upper:
        return "ACIERTO"
    if "FALLO" in upper:
        return "FALLO"
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
        codigo = sel.replace(" ", "")
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

    for e in eventos:
        if e.get("status") != "settled":
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
