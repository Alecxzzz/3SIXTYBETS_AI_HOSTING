# -*- coding: utf-8 -*-
"""Vista previa del motor Elo + Monte Carlo: que apostaria HOY, sin publicar.

Por que existe
--------------
Antes de encender el motor en el generador del dashboard hay que poder mirarlo
sin consecuencias: este script imprime, para cada partido de la ventana, el
marcador esperado, la probabilidad de cada mercado real de Doradobet y su edge,
y marca cual seria el pick. NO escribe en `ai_picks` ni en `ai_picks_prob`: solo
deja los ratings en `elo_ratings` (cache) para no volver a descargar la
temporada en cada ejecucion.

Uso:
    python motor_preview.py                # NBA de hoy
    python motor_preview.py --top 8        # 8 mercados por partido
    python motor_preview.py --sport nba --min-edge 0.0   # ver TODOS los edges
"""

import argparse
import sys

import sports
from backend.apuestas import motor

# La consola de Windows (cp1252) destroza los acentos de los mercados
# ("Hándicap" -> "Hßndicap") y hace ilegible la salida. En UTF-8 se ve bien.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 - en algunos entornos no se puede reconfigurar
    pass


def _partidos(sport: str):
    """Partidos de la ventana con los ids de ESPN que necesita Elo."""
    datos = sports.get_sport_games(sport)
    salida = []
    for g in datos.get("games") or []:
        if g.get("state") == "post":
            continue
        home, away = g.get("home") or {}, g.get("away") or {}
        if not home.get("id") or not away.get("id"):
            continue
        salida.append({
            "sport": sport,
            "event_id": str(g.get("id") or ""),
            "home_name": home.get("name") or "",
            "away_name": away.get("name") or "",
            "home_id": str(home.get("id") or ""),
            "away_id": str(away.get("id") or ""),
            "date": g.get("date") or "",
            "state": g.get("state"),
        })
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description="Preview del motor Elo + Monte Carlo")
    ap.add_argument("--sport", default="nba")
    ap.add_argument("--top", type=int, default=6, help="mercados a mostrar por partido")
    ap.add_argument("--min-edge", type=float, default=None,
                    help="umbral de edge para marcar el pick (por defecto el del motor)")
    args = ap.parse_args()

    partidos = _partidos(args.sport)
    if not partidos:
        print(f"No hay partidos de {args.sport} en la ventana de analisis.")
        return 0

    print(f"\nPartidos de {args.sport.upper()} en ventana: {len(partidos)}")
    print("=" * 100)

    con_pick = 0
    for p in partidos:
        print(f"\n{p['away_name']} @ {p['home_name']}   [{p['state']}]   {p['date']}")
        try:
            from cuotas_doradobet import mercados_reales

            mercados = mercados_reales(args.sport, p["home_name"], p["away_name"],
                                       p["date"], motor.MIN_CUOTA, motor.MAX_CUOTA)
        except Exception as exc:  # noqa: BLE001
            print(f"  [sin cuotas] {exc}")
            continue

        evaluacion = motor.evaluar(p, mercados, args.sport, semilla=7)
        if not evaluacion.get("ok"):
            print(f"  [no evaluable] {evaluacion.get('motivo')}")
            continue

        pre = evaluacion["pre"]
        print(f"  Elo {pre['elo_local']:.0f} vs {pre['elo_visita']:.0f} "
              f"(localia {pre['elo_diff']:+.0f}) | esperado "
              f"{pre['mu_local']:.1f} - {pre['mu_visita']:.1f} | "
              f"total {pre['total_esperado']:.1f} | mercado: {len(mercados)} lineas, "
              f"{len(evaluacion['candidatos'])} simulables, "
              f"{evaluacion.get('descartados_discrepancia', 0)} descartadas por "
              f"discrepancia con la cuota")

        if not evaluacion["candidatos"]:
            print("  (ningun mercado simulable)")
            continue

        pick = motor.decidir(p, mercados, args.sport, min_edge=args.min_edge,
                            semilla=7)
        print(f"  {'mercado':<52} {'cuota':>6} {'prob':>7} {'IC':>12} {'edge':>8}")
        print("  " + "-" * 92)
        for c in evaluacion["candidatos"][:args.top]:
            e = c["entrada"]
            marca = " >>" if pick and e.get("selection") == pick.get("selection") \
                and e.get("market") == pick.get("market") else "   "
            ic = f"{c['prob_low'] * 100:.0f}-{c['prob_high'] * 100:.0f}%"
            print(f"{marca}{str(e.get('titulo'))[:50]:<52} {c['cuota']:>6.3f} "
                  f"{c['prob'] * 100:>6.1f}% {ic:>12} {c['edge'] * 100:>+7.1f}%")
        if pick:
            con_pick += 1
            print(f"  PICK: {pick['selection']} @ {pick['odds']} "
                  f"[{pick['confidence']}] edge {pick['_edge'] * 100:+.1f}%")
        else:
            print("  Sin pick: ningun mercado supera el umbral con margen de seguridad.")

    print("\n" + "=" * 100)
    print(f"Partidos con pick: {con_pick}/{len(partidos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
