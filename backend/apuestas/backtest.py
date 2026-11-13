# -*- coding: utf-8 -*-
"""BACKTEST del motor: mide la calidad REAL de las probabilidades publicadas.

Por que existe
--------------
`ai_picks` guarda la cuota y el resultado, pero NO la probabilidad que dijo el
modelo. Sin eso solo se puede medir "cuantos acerto" (una moneda al aire acierta
50%), no si las probabilidades estaban BIEN CALIBRADAS. Este script cruza
`ai_picks_prob` con el resultado ya resuelto y calcula:

  * Brier y log-loss: castigan la confianza mal puesta, no solo el fallo.
  * Curva de calibracion por decil: "cuando dije 60%, paso el 60%?".
  * ROI: lo unico que paga las facturas, pero ruidoso con pocas muestras.

Uso:
    python -m backend.apuestas.backtest
    python -m backend.apuestas.backtest --sport nba
"""

import argparse
import math
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

import db  # noqa: E402


CONSULTA = """
select p.prob, p.prob_low, p.edge, p.cuota, p.market, p.sport, p.modelo_version,
       a.result
from ai_picks_prob p
join ai_picks a on a.id = p.pick_id
where p.descartado = 0
  and a.result in ('ACIERTO', 'FALLO')
"""


def _deciles(filas):
    """Curva de calibracion: probabilidad media vs frecuencia real por decil."""
    cubos = {}
    for f in filas:
        p = float(f["prob"] or 0)
        cubo = min(9, int(p * 10))
        cubos.setdefault(cubo, []).append(f)
    salida = []
    for cubo in sorted(cubos):
        filas_cubo = cubos[cubo]
        n = len(filas_cubo)
        aciertos = sum(1 for f in filas_cubo if f["result"] == "ACIERTO")
        p_media = sum(float(f["prob"] or 0) for f in filas_cubo) / n
        salida.append({
            "decil": f"{cubo * 10}-{cubo * 10 + 10}%",
            "n": n,
            "prob_media": round(p_media, 4),
            "frecuencia": round(aciertos / n, 4),
            "error": round(aciertos / n - p_media, 4),
        })
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest del motor Elo + Monte Carlo")
    ap.add_argument("--sport", default=None, help="filtrar por deporte (nba, soccer...)")
    args = ap.parse_args()

    consulta = CONSULTA
    params = None
    if args.sport:
        consulta += " and p.sport = %s"
        params = (args.sport,)
    filas = db.run_query(consulta, params) or []
    if not filas:
        print("Sin picks resueltos con probabilidad registrada todavia.")
        print("Se llena solo: cada pick del motor escribe en ai_picks_prob y el")
        print("resolutor marca ACIERTO/FALLO. Vuelve en unos dias.")
        return 0

    n = len(filas)
    brier = logloss = roi = 0.0
    for f in filas:
        p = min(max(float(f["prob"] or 0), 1e-6), 1 - 1e-6)
        acierto = 1.0 if f["result"] == "ACIERTO" else 0.0
        cuota = float(f["cuota"] or 0)
        brier += (p - acierto) ** 2
        logloss -= acierto * math.log(p) + (1 - acierto) * math.log(1 - p)
        roi += (cuota - 1.0) if acierto else -1.0

    print(f"\nPicks resueltos con probabilidad: {n}")
    print("=" * 60)
    print(f"  Brier        : {brier / n:.5f}   (moneda al aire: 0.25000)")
    print(f"  Log-loss     : {logloss / n:.5f}")
    print(f"  ROI          : {roi / n * 100:+.2f}% por apuesta")
    print(f"  Acierto      : "
          f"{100 * sum(1 for f in filas if f['result'] == 'ACIERTO') / n:.1f}%")
    print("-" * 60)
    print("  Calibracion (lo que importa: la probabilidad dicha vs la real)")
    for d in _deciles(filas):
        print(f"    {d['decil']:>8}  n={d['n']:<4} "
              f"dicho {d['prob_media'] * 100:.1f}%  "
              f"paso {d['frecuencia'] * 100:.1f}%  "
              f"error {d['error'] * 100:+.1f} pts")
    print("=" * 60)
    if n < 100:
        print("  AVISO: con menos de ~100 picks resueltos estos numeros son ruido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
