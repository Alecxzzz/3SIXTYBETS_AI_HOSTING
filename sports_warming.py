"""
Calentador de cache para Estadisticas.

Mantiene los scoreboards calientes (thread daemon desde el startup de
main.py): refresca la vista general de cada deporte (con TODAS las ligas de
futbol incluidas) y las ligas individuales de futbol cada WARM_INTERVAL_S.

Beneficios:
- Estadisticas responde al instante: casi siempre hay cache fresco (<60s).
- El trafico hacia ESPN queda concentrado en UN solo hilo con pausas, en vez
  de rafagas paralelas de usuarios: menos bloqueos 403/429 de Akamai.
- Junto con el lock anti-duplicados de get_sport_games, nunca hay dos
  peticiones simultaneas contra el mismo scoreboard de ESPN.
"""

import time
import threading
import traceback

import sports

WARM_INTERVAL_S = 45
PAUSA_ENTRE_DEPORTES_S = 2.5   # respiro entre deporte y deporte (cortesia ESPN)
_FIRST_RUN = threading.Event()
_lock = threading.Lock()


def _warm_sport(sport: str) -> None:
    try:
        t0 = time.time()
        data = sports.get_sport_games(sport)
        n = len(data.get("games") or [])
        err = data.get("error")
        print(f"[warm] {sport}: {n} partidos, {err or 'ok'} "
              f"({time.time() - t0:.1f}s)", flush=True)
    except Exception as exc:
        print(f"[warm] {sport} fallo: {exc}", flush=True)


def _warm_soccer_leagues() -> None:
    """Ligas individuales de futbol (para el selector del frontend)."""
    for codigo in sports.SOCCER_LEAGUES:
        try:
            data = sports.get_sport_games("soccer", codigo)
            time.sleep(0.5)  # respiro entre liga y liga
        except Exception as exc:
            print(f"[warm] liga {codigo} fallo: {exc}", flush=True)
            continue
        # Si la general de futbol se lleno bien, esto es mantenimiento ligero.


def _loop() -> None:
    print("[warm] calentador de cache iniciado "
          f"(cada {WARM_INTERVAL_S}s)", flush=True)
    vuelta = 0
    while True:
        inicio = time.time()
        vuelta += 1
        try:
            for sport in sports.SPORTS:
                _warm_sport(sport)
                time.sleep(PAUSA_ENTRE_DEPORTES_S)
            # Las ligas individuales solo cada 3 vueltas (~2 min), para no
            # generar trafico extra innecesario contra ESPN.
            if vuelta % 3 == 0:
                _warm_soccer_leagues()
        except Exception:
            print("[warm] error en vuelta:\n" + traceback.format_exc(),
                  flush=True)
        # Dormir el resto del intervalo
        restante = WARM_INTERVAL_S - (time.time() - inicio)
        if restante > 0:
            time.sleep(restante)


def iniciar_calentador() -> None:
    """Arranca el hilo daemon (idempotente)."""
    if _FIRST_RUN.is_set():
        return
    with _lock:
        if _FIRST_RUN.is_set():
            return
        _FIRST_RUN.set()
    hilo = threading.Thread(target=_loop, name="sports-warming", daemon=True)
    hilo.start()
