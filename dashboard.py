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
from datetime import datetime, timezone

import db

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
2. NUNCA repitas el mismo mercado en los diferentes o mismos partidos.
3. SIEMPRE ve variando las opciones: no te centres solo en 1X2 o goles.
4. Busca SIEMPRE la apuesta mas FACIL de acertar CON VALOR (cuota justa vs probabilidad real).
5. Respeta los minimos indicados (cuotas minimas, handicap minimo, under mas bajo en NBA/tenis).
6. Responde EXCLUSIVAMENTE con un JSON valido, sin texto extra, con esta forma exacta:
{
  "market": "<nombre exacto del mercado elegido del catalogo>",
  "selection": "<seleccion concreta: equipo A/B, SI/NO, over/under X.X, etc>",
  "odds": <cuota decimal estimada o null>,
  "confidence": "<ALTA|MEDIA|BAJA>",
  "rationale": "<1-2 frases del edge en espanol>"
}
"""


# ============================================================
# GENERACION DE PICKS
# ============================================================


def _partidos_hoy():
    """Trae los partidos de hoy de todos los deportes del dashboard."""
    import sports

    partidos = []
    for sport in DEPORTES_DASHBOARD:
        try:
            data = sports.get_sport_games(sport)
            for g in data.get("games", []):
                if g.get("state") == "post":
                    continue  # ya finalizados: no generar pick nuevo
                equipos = g.get("teams") or []
                nombres = [t.get("name", "?") for t in equipos]
                partidos.append({
                    "sport": sport,
                    "label": data.get("label", sport),
                    "event_id": str(g.get("id", "")),
                    "event_name": " vs ".join(nombres) if nombres else "?",
                    "home": nombres[1] if len(nombres) > 1 else "?",
                    "away": nombres[0] if nombres else "?",
                    "date": g.get("date", ""),
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
            content = (
                data.get("choices", [{}])[0].get("message", {}).get("content") or ""
            )
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


def generar_picks_dia(max_partidos: int = 40) -> dict:
    """Genera picks automaticos para los partidos de hoy.

    Idempotente: salta partidos que ya tienen pick guardado hoy.
    Nunca repite el mismo mercado (normalizado) en toda la jornada.
    """
    partidos = _partidos_hoy()
    generados = 0
    omitidos = 0
    errores = 0

    mercados_usados = {_market_norm(r["market"]) for r in (db.list_picks_hoy() or [])}

    for p in partidos[:max_partidos]:
        if db.pick_existe(p["event_id"]):
            omitidos += 1
            continue

        label, mercados = MERCADOS_POR_DEPORTE[p["sport"]]
        if not [m for m in mercados if _market_norm(m) not in mercados_usados]:
            break  # ya se usaron todos los mercados del catalogo hoy

        mensaje = (
            f"Partido: {p['away']} (visitante) vs {p['home']} (local)\n"
            f"Deporte: {label}\nFecha/hora: {p['date']}\n\n"
            f"Elige UN solo mercado del catalogo de {label} (que NO sea uno de estos ya "
            f"usados hoy: {', '.join(list(mercados_usados)[:15]) or 'ninguno'}). "
            f"Devuelve el JSON del pick."
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
        )
        if creado:
            generados += 1
            mercados_usados.add(_market_norm(market))

    return {
        "partidos": len(partidos),
        "generados": generados,
        "omitidos_ya_con_pick": omitidos,
        "errores": errores,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# RESOLUCION DE PICKS (ACIERTO / FALLO)
# ============================================================


def _resolver_pick_con_ia(pick: dict):
    """Pregunta a la IA si el pick fue ACIERTO o FALLO con el resultado final."""
    import sports

    try:
        detail = sports.get_game_detail(pick["sport"], pick["event_id"])
    except Exception:
        return None

    teams = detail.get("teams", [])
    if len(teams) < 2 or detail.get("state") != "post":
        return None

    marcador = " vs ".join(
        f"{t.get('name', '?')} {t.get('score', '-')}" for t in teams
    )
    mensaje = (
        f"Pick realizado: mercado '{pick['market']}' - seleccion '{pick['selection']}'.\n"
        f"Partido: {pick['event_name']} (deporte {pick.get('sport_label', '')}).\n"
        f"Resultado final: {marcador}.\n\n"
        f"Con ese resultado final, ¿el pick fue ACIERTO o FALLO?\n"
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


def resolver_picks_finalizados() -> dict:
    """Resuelve los picks pendientes cuyos partidos ya terminaron."""
    pendientes = db.list_picks_pendientes() or []
    resueltos = 0
    for pick in pendientes:
        resultado = _resolver_pick_con_ia(pick)
        if resultado:
            db.update_pick_result(pick["id"], resultado)
            resueltos += 1
    return {"pendientes": len(pendientes), "resueltos": resueltos}


# ============================================================
# RESUMEN PARA EL DASHBOARD
# ============================================================


def resumen_dashboard(username: str) -> dict:
    """Bienvenida + stats del dashboard.

    - pronosticos_del_dia: todos los picks de hoy (todos los deportes).
    - acertados: SOLO los picks con resultado ACIERTO (los fallados nunca
      se muestran al usuario).
    """
    picks = db.list_picks_hoy() or []
    aciertos = [p for p in picks if p.get("result") == "ACIERTO"]
    historial = db.count_aciertos_historico() or {}

    por_deporte = {}
    for p in picks:
        key = p.get("sport_label") or p.get("sport")
        por_deporte[key] = por_deporte.get(key, 0) + 1

    return {
        "welcome": f"Bienvenido, {username}",
        "stats": {
            "pronosticos_del_dia": len(picks),
            "pronosticos_acertados_por_la_ia": len(aciertos),
            "por_deporte": por_deporte,
            "historico_aciertos": historial.get("aciertos", 0),
            "historico_resueltos": historial.get("resueltos", 0),
        },
        "pronosticos_del_dia": picks,
        "pronosticos_acertados": aciertos,
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
