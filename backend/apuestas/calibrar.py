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


# --------------------------------------------------------------------------
# FUTBOL: metricas de 1X2 (tres resultados, no dos)
# --------------------------------------------------------------------------
# En futbol NO vale el Brier de "gana el local": con una normal, P(margen=0) es
# cero y el 27% de empates se reparte entre los dos equipos. Medido asi, un
# modelo perfectamente razonable saca 0.25 (lo mismo que tirar una moneda) y se
# concluye, falsamente, que Elo no sirve para futbol.
#
# La forma correcta es la probabilidad de CADA resultado con Poisson, que es lo
# que simula el motor. Se evalua en cerrado (0..MAX_GOLES) porque es exacto y
# permite medir 2.000 partidos en un segundo.
MAX_GOLES = 10


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def prob_1x2(mu_local: float, mu_visita: float) -> Dict[str, float]:
    """{local, empate, visita} con dos Poisson independientes.

    Es el limite de `simulacion.simular_futbol` con infinitas simulaciones (sin
    el factor comun de correlacion), asi que sirve para medir el mismo modelo
    que se publica, no otro.
    """
    local = [ _poisson_pmf(k, mu_local) for k in range(MAX_GOLES + 1) ]
    visita = [ _poisson_pmf(k, mu_visita) for k in range(MAX_GOLES + 1) ]
    p_local = p_empate = p_visita = 0.0
    for gh in range(MAX_GOLES + 1):
        for ga in range(MAX_GOLES + 1):
            p = local[gh] * visita[ga]
            if gh > ga:
                p_local += p
            elif gh == ga:
                p_empate += p
            else:
                p_visita += p
    total = p_local + p_empate + p_visita
    if total <= 0:
        return {"local": 1 / 3, "empate": 1 / 3, "visita": 1 / 3}
    return {"local": p_local / total, "empate": p_empate / total,
            "visita": p_visita / total}


def _brier_1x2(probs: Dict[str, float], margen: float) -> float:
    """Brier multiclase: suma de (p_i - y_i)^2 sobre los tres resultados."""
    real = ("local" if margen > 0 else ("empate" if margen == 0 else "visita"))
    return sum((probs[k] - (1.0 if k == real else 0.0)) ** 2
               for k in ("local", "empate", "visita"))


def medir(muestras: List[Dict[str, Any]], calib: Dict[str, Any],
          sport: str) -> Dict[str, Any]:
    """Brier, log-loss y MAE del modelo contra los dos baselines."""
    hfa = elo._cfg(sport).get("hfa", 0.0)
    a, b, c = calib["a"], calib["b"], calib["c"]
    sigma = calib["sigma_margen"] or 1.0

    brier = logloss = mae = 0.0
    brier_base = brier_local = mae_base = 0.0
    n = 0
    futbol = elo.es_futbol(sport)
    brier_1x2 = brier_1x2_base = logloss_1x2 = 0.0
    # Tasas base (local/empate/visita) de la PROPIA muestra: es el baseline
    # honesto en futbol. Batir al 50/50 no dice nada; batir a "predice siempre
    # las frecuencias historicas" si dice algo.
    cuenta = {"local": 0, "empate": 0, "visita": 0}
    for m in muestras:
        cuenta["local" if m["margen"] > 0 else
              ("empate" if m["margen"] == 0 else "visita")] += 1
    tasa = ({k: v / len(muestras) for k, v in cuenta.items()} if muestras
            else {"local": 1 / 3, "empate": 1 / 3, "visita": 1 / 3})
    total_medio = float(calib.get("total_medio") or 0.0) or 2.7
    t0, t1 = calib.get("t0") or 0.0, calib.get("t1") or 0.0

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

        if futbol:
            # Mismo reparto margen/total que usa `motor.predecir`, con la forma
            # que habia ANTES del partido (total_h/total_a). Sin ella se usa la
            # media de la liga: mejor eso que inventarse un ritmo.
            ritmo = (m["total_h"] + m["total_a"]) / 2.0 \
                if m.get("total_h") and m.get("total_a") else total_medio
            total_esp = t0 + t1 * ritmo if (t0 or t1) else total_medio
            mu_h = max(0.02, (total_esp + margen_esp) / 2.0)
            mu_a = max(0.02, (total_esp - margen_esp) / 2.0)
            probs = prob_1x2(mu_h, mu_a)
            real = ("local" if m["margen"] > 0 else
                    ("empate" if m["margen"] == 0 else "visita"))
            brier_1x2 += _brier_1x2(probs, m["margen"])
            brier_1x2_base += _brier_1x2(tasa, m["margen"])
            logloss_1x2 -= math.log(max(probs[real], 1e-6))

    if not n:
        return {"ok": False, "motivo": "sin muestras historicas"}

    salida = {
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
    if futbol and n:
        salida.update({
            # Brier multiclase (0 = perfecto, 2 = siempre al reves). Referencia:
            # 1 - sum(tasa^2) es lo que saca un modelo que solo conoce las
            # frecuencias; el modelo tiene que bajarlo de ahi.
            "brier_1x2": round(brier_1x2 / n, 5),
            "brier_1x2_tasas_base": round(brier_1x2_base / n, 5),
            "log_loss_1x2": round(logloss_1x2 / n, 5),
            "mejora_1x2_pct": round(100 * (1 - (brier_1x2 / n) / (brier_1x2_base / n)), 2),
            "tasa_local": round(tasa["local"], 4),
            "tasa_empate": round(tasa["empate"], 4),
            "tasa_visita": round(tasa["visita"], 4),
        })
    return salida


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
    if "brier_1x2" in metricas:
        print("-" * 68)
        print(f"  1X2 (Brier multiclase, es la metrica que importa aqui):")
        print(f"    modelo      : {metricas['brier_1x2']:.5f}")
        print(f"    tasas base  : {metricas['brier_1x2_tasas_base']:.5f}  "
              f"(local {metricas['tasa_local']:.1%} | empate "
              f"{metricas['tasa_empate']:.1%} | visita {metricas['tasa_visita']:.1%})")
        print(f"    mejora      : {metricas['mejora_1x2_pct']:+.2f}%   "
              f"log-loss {metricas['log_loss_1x2']:.5f}")
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
    # En futbol el liston de verdad es batir las TASAS BASE del propio campeonato:
    # decir "45% local / 27% empate / 28% visita" ya acierta bastante, y un Elo
    # que no lo mejora no sirve para decidir nada.
    if "brier_1x2" in metricas:
        if metricas["brier_1x2"] < metricas["brier_1x2_tasas_base"]:
            veredicto.append("OK  1X2 mejor que las tasas base de la liga")
        else:
            veredicto.append("BAD 1X2 NO mejora a las tasas base de la liga")
    for linea in veredicto:
        print(f"  {linea}")
    print("=" * 68)
    print(f"  Guardado en BD: {_persistir(sport, datos, calib)}")
    print()
    return 0 if all(v.startswith("OK") for v in veredicto) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

