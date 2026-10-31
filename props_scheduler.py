# -*- coding: utf-8 -*-
"""
Refrescador AUTOMATICO del apartado de props de jugador (/props).

Cada 90 minutos (cada hora y media):

1. Mira el CALENDARIO real del dia (ESPN) de cada deporte activo. Los
   partidos nuevos traen props nuevas sin tocar nada a mano: no hay ninguna
   lista fija que mantener.
2. Por cada partido que aun no empezo pide al bookmaker sus props de jugador
   y la racha de cada jugador (ultimos N partidos jugados ANTES del partido).
3. Guarda el snapshot en la tabla `player_props` (idempotente por clave
   unica: relanzar el ciclo refresca cuotas/racha, no duplica).
4. Poda el historico mas viejo que RETENCION_DIAS.

Si ESPN o el bookmaker fallan, NO se borra nada: se conserva el ultimo
snapshot bueno y se reintenta en el siguiente ciclo. Arranca como hilo daemon
desde el startup de main.py, igual que event_scheduler y sports_warming.
"""

import threading
import time
import traceback
from datetime import datetime, timezone

import db
from backend.props import service

INTERVAL_S = 90 * 60          # cada hora y media
PRIMER_DELAY_S = 45           # deja terminar el startup
N_PARTIDOS = 5                # "ultimos 5 partidos"
MAX_EVENTOS = 6               # partidos por deporte y ciclo (control de coste)
MAX_JUGADORES = 10            # jugadores por partido
RETENCION_DIAS = 90

# MLB esta fuera de temporada (ultimo partido 2026-09-27). El codigo lo
# soporta; se activa solo cuando ESPN vuelva a publicar partidos.
DEPORTES = ("soccer", "nba", "nfl")

_lock = threading.Lock()
_arrancado = threading.Event()
_salud = {"ultima": None, "ok": None, "detalle": None, "props": 0, "ms": 0}


def salud() -> dict:
    """Estado del ultimo ciclo (para /dashboard/salud o diagnostico)."""
    return dict(_salud)


def refresh_once(usar_cache: bool = False) -> dict:
    """Una corrida completa. Devuelve el resumen (tambien se loguea)."""
    inicio = time.time()
    total, partes = 0, []
    for sport in DEPORTES:
        try:
            resultado = service.props_deporte(
                sport,
                n=N_PARTIDOS,
                limite_eventos=MAX_EVENTOS,
                max_jugadores=MAX_JUGADORES,
                usar_cache=usar_cache,
            )
        except Exception as exc:  # noqa: BLE001
            partes.append(f"{sport}: error {exc!r}")
            continue
        props = resultado.get("props") or []
        if not props:
            partes.append(f"{sport}: sin props ({resultado.get('motivo') or 'sin partidos'})")
            continue
        guardadas = db.upsert_player_props(props, reemplazar_sport=sport)
        total += guardadas
        partes.append(
            f"{sport}: {guardadas} props de "
            f"{resultado.get('eventos_con_props')}/{resultado.get('eventos')} partidos"
        )

    try:
        podadas = db.purgar_player_props(RETENCION_DIAS)
        if podadas:
            partes.append(f"podadas {podadas}")
    except Exception as exc:  # noqa: BLE001
        partes.append(f"purga: {exc!r}")

    # A1: cerrar las props de partidos ya jugados (ACIERTO / FALLO).
    try:
        cierre = service.cerrar_props()
        if cierre.get("revisadas"):
            partes.append(
                f"cierre: {cierre['cerradas']}/{cierre['revisadas']} props "
                f"({cierre['sin_partido']} sin partido aun)"
            )
    except Exception as exc:  # noqa: BLE001
        partes.append(f"cierre: {exc!r}")

    resumen = "; ".join(partes) or "sin actividad"
    _salud.update({
        "ultima": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": total > 0,
        "detalle": resumen,
        "props": total,
        "ms": int((time.time() - inicio) * 1000),
    })
    return dict(_salud)


def _loop() -> None:
    time.sleep(PRIMER_DELAY_S)
    print(f"[PropsScheduler] iniciado (cada {INTERVAL_S // 60} min)", flush=True)
    while True:
        inicio = time.time()
        try:
            estado = refresh_once()
            print(f"[PropsScheduler] {estado['detalle']} ({estado['ms']} ms)", flush=True)
        except Exception:
            print("[PropsScheduler] error en el ciclo:\n" + traceback.format_exc(),
                  flush=True)
        restante = INTERVAL_S - (time.time() - inicio)
        time.sleep(max(60, restante))


def iniciar_scheduler() -> None:
    """Arranca el hilo daemon (idempotente)."""
    if _arrancado.is_set():
        return
    with _lock:
        if _arrancado.is_set():
            return
        _arrancado.set()
    threading.Thread(target=_loop, name="props-scheduler", daemon=True).start()
