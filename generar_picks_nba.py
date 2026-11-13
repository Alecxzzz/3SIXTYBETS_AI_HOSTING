# -*- coding: utf-8 -*-
"""Genera los picks de NBA AHORA con el motor Elo + Monte Carlo.

Por que existe
--------------
`generar_picks_dia()` recorre los cinco deportes y, en futbol/MLB, gasta
llamadas de IA. Para probar el motor nuevo en un solo deporte hace falta poder
limitarlo: este script deja `DEPORTES_DASHBOARD = ["nba"]` y llama al mismo
generador de siempre, asi que:

  * los picks se eligen con Elo + Monte Carlo (backend/apuestas/motor.py),
  * pasan por TODOS los filtros de calidad del dashboard,
  * caen al criterio anterior si el motor no ve valor,
  * quedan registrados en ai_picks y su probabilidad en ai_picks_prob.

NO es un motor paralelo: es el mismo generador con un solo deporte encendido.

Uso:
    python generar_picks_nba.py
    python generar_picks_nba.py --forzar     # reemplaza los picks ya existentes
"""

import argparse
import sys

import dashboard as d


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera picks de NBA con Elo + MC")
    ap.add_argument("--forzar", action="store_true",
                    help="reemplaza los picks que ya existan del evento")
    ap.add_argument("--max", type=int, default=40, help="maximo de partidos a mirar")
    args = ap.parse_args()

    if "nba" not in d.MOTOR_MC_DEPORTES:
        print("OJO: el motor Elo+MC esta apagado para NBA "
              "(DASHBOARD_MOTOR_MC). Se generaria con el criterio anterior.")

    # Un solo deporte: ni futbol ni MLB ni tenis en esta corrida.
    d.DEPORTES_DASHBOARD = ["nba"]

    print(f"Generando picks de NBA (motor Elo+MC activo en: "
          f"{sorted(d.MOTOR_MC_DEPORTES) or 'ninguno'})...", flush=True)
    stats = d.generar_picks_dia(max_partidos=args.max, forzar=args.forzar)

    print("\n" + "=" * 60)
    for clave, valor in stats.items():
        print(f"  {clave}: {valor}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
