# -*- coding: utf-8 -*-
"""CALIBRA el motor Elo: mide si el modelo SIRVE antes de usarlo para apostar.

Por que existe
--------------
Un motor que solo "suena bien" no vale nada. Este script coge todos los
partidos historicos (2-3 temporadas de ESPN), los reprocesa en orden
cronologico y compara las predicciones con lo que paso de verdad:

  * Brier y log-loss de "gana el local" (probabilidad, no acierto bruto: una
    probabilidad mal calibrada se penaliza aunque acierte).
  * MAE del margen en puntos.

Contra dos baselines honestos:
  * "siempre 50%" (no saber nada)
  * "siempre gana el local" (0.58, el sesgo mas fuerte y gratuito de la liga)

Criterio de cordura: el modelo tiene que batir a LOS DOS. Si no, no se activa y
se reportan los numeros en crudo.

Uso:
    python -m backend.apuestas.calibrar nba
    python -m backend.apuestas.calibrar nba --temporadas 3 --json
"""

import json
import math
import os
import sys
from typing import Any, Dict, List

if __package__ in (None, ""):  # permite `python backend/apuestas/calibrar.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from backend.apuestas import elo  # noqa: E402


def _cdf(z: float) -> float:
    """Normal estandar acumulada, sin scipy (igual que ai/analista_nba.py)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _prob_local(margen_esp: float, sigma: float) -> float:
    """P(margen > 0) con margen ~ Normal(margen_esp, sigma)."""
    if sigma <= 0:
        return 1.0 if margen_esp > 0 else 0.0
    return _cdf(margen_esp / sigma)


def medir(muestras: List[Dict[str, Any]], calib: Dict[str, Any],
          sport: str) -> Dict[str, Any]:
    """Brier, log-loss y MAE del modelo contra los dos baselines."""
    hfa = (elo.CONFIG.get(sport) or {}).get("hfa", 0.0)
    a, b, c = calib["a"], calib["b"], calib["c"]
    sigma = calib["sigma_margen"] or 1.0

    brier = logloss = mae = 0.0
    brier_base = brier_local = mae_base = 0.0
    n = 0
    for m in muestras:
        rest_h = m["descanso_h"] if m["descanso_h"] is not None else 4.0
        rest_a = m["descanso_a"] if m["descanso_a"] is not None else 4.0
        d = (m["elo_h"] + hfa - m["elo_a"]) / 100.0
        margen_esp = a + b * d + c * (rest_h - rest_a)
        p = min(max(_prob_local(margen_esp, sigma), 1e-6), 1 - 1e-6)
        gano_local = 1.0 if m["margen"] > 0 else 0.0

        brier += (p - gano_local) ** 2
        logloss -= gano_local * math.log(p) + (1 - gano_local) * math.log(1 - p)
        mae += abs(margen_esp - m["margen"])
        brier_base += (0.5 - gano_local) ** 2
        brier_local += (0.58 - gano_local) ** 2
        mae_base += abs(m["margen"])
        n += 1

    if not n:
        return {"ok": False, "motivo": "sin muestras historicas"}

    return {
        "ok": True,
        "n_partidos": n,
        "brier": round(brier / n, 5),
        "log_loss": round(logloss / n, 5),
        "mae_margen": round(mae / n, 3),
        "baseline_brier_50_50": round(brier_base / n, 5),
        "baseline_brier_local": round(brier_local / n, 5),
        "baseline_mae_margen": round(mae_base / n, 3),
        "mejora_brier_pct": round(100 * (1 - (brier / n) / (brier_base / n)), 2),
        "mejora_mae_pct": round(100 * (1 - (mae / n) / (mae_base / n)), 2),
    }


def _persistir(sport: str, datos: Dict[str, Any], calib: Dict[str, Any]) -> bool:
    """Guarda ratings + calibracion. Se hace AL FINAL, despues de imprimir: si
    la BD esta lenta o mal configurada, los numeros ya se han visto."""
    ratings = datos.get("ratings") or {}
    for tid, resumen in (datos.get("forma") or {}).items():
        if resumen and tid in ratings:
            ratings[tid].update(resumen)
    return elo.guardar(sport, ratings, calib)


def main(argv: List[str]) -> int:
    sport = (argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "nba").lower()
    temporadas = 3
    if "--temporadas" in argv:
        try:
            temporadas = int(argv[argv.index("--temporadas") + 1])
        except (IndexError, ValueError):
            temporadas = 3
    solo_json = "--json" in argv

    if not solo_json:
        print(f"[calibrar] Procesando {temporadas} temporadas de {sport.upper()} "
              f"desde ESPN (puede tardar ~1 min)...", flush=True)

    datos = elo.construir(sport, temporadas)
    muestras = datos.get("muestras") or []
    liga = datos.get("liga") or {}
    if not muestras:
        print(f"[calibrar] FAIL: ESPN no devolvio partidos con marcador para {sport}")
        return 1

    calib = elo.ajustar(muestras, liga, sport)
    metricas = medir(muestras, calib, sport)
    resultado = {"calibracion": calib, "metricas": metricas, "liga": liga,
                 "equipos": len(datos.get("ratings") or {})}

    if solo_json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
        _persistir(sport, datos, calib)
        return 0

    print()
    print("=" * 68)
    print(f"  CALIBRACION {sport.upper()}  |  {metricas.get('n_partidos')} partidos")
    print("=" * 68)
    print(f"  margen = {calib['a']:+.2f} {calib['b']:+.3f}*elo_diff/100 "
          f"{calib['c']:+.3f}*descanso_diff")
    print(f"  total  = {calib['t0']:+.2f} {calib['t1']:+.3f}*ritmo")
    print(f"  sigma margen {calib['sigma_margen']} | sigma total {calib['sigma_total']} "
          f"| sigma equipo {calib['sigma_equipo']}")
    print(f"  rho equipos {calib['rho_equipos']} | prorroga "
          f"{calib['p_ot'] * 100:.2f}% | total medio {calib['total_medio']}")
    print(f"  control: sd margen bruta {calib.get('sd_margen_bruta')} | "
          f"sd total bruta {calib.get('sd_total_bruta')} | "
          f"ventaja local {calib.get('ventaja_local')} pts "
          f"(hfa efectivo {calib.get('hfa_efectivo')})")
    print(f"  total reciente (21 dias) {calib.get('total_reciente')} | "
          f"total medio 3 temporadas {calib.get('total_medio')} | "
          f"descanso en el ajuste: "
          f"{'NO (columna constante)' if calib.get('sin_descanso') else 'si'}")
    print("-" * 68)
    print(f"  Brier modelo    : {metricas['brier']:.5f}   "
          f"(50/50: {metricas['baseline_brier_50_50']:.5f} | "
          f"siempre local: {metricas['baseline_brier_local']:.5f})")
    print(f"  Mejora vs 50/50 : {metricas['mejora_brier_pct']:+.2f}%")
    print(f"  Log-loss        : {metricas['log_loss']:.5f}")
    print(f"  MAE del margen  : {metricas['mae_margen']:.2f} pts   "
          f"(baseline {metricas['baseline_mae_margen']:.2f} pts, "
          f"mejora {metricas['mejora_mae_pct']:+.2f}%)")
    print("-" * 68)

    # Criterio de cordura: en NBA el margen medio absoluto ronda 11-12 puntos,
    # asi que un modelo que no baja de ahi no aporta nada sobre "no saber".
    veredicto = []
    if metricas["brier"] < metricas["baseline_brier_local"]:
        veredicto.append("OK  Brier mejor que 'siempre gana el local'")
    else:
        veredicto.append("BAD Brier NO mejora a 'siempre gana el local'")
    if metricas["mae_margen"] < metricas["baseline_mae_margen"]:
        veredicto.append("OK  MAE del margen mejor que predecir 0")
    else:
        veredicto.append("BAD MAE del margen NO mejora a predecir 0")
    for linea in veredicto:
        print(f"  {linea}")
    print("=" * 68)
    print(f"  Guardado en BD: {_persistir(sport, datos, calib)}")
    print()
    return 0 if all(v.startswith("OK") for v in veredicto) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

