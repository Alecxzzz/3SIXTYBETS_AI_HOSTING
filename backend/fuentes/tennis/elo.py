# -*- coding: utf-8 -*-
"""ELO de tenis (superficie + ranking), con parametros VALIDADOS por backtest.

Backtest real sobre ATP 2018-2023 -> 2024-2026 (7.469 partidos
out-of-sample):

    moneda (50/50)                     Brier 0.2500
    ranking (logistico, b=0.5)         Brier 0.2205
    Elo K=32 sin superficie            Brier 0.2203
    Elo K=32 + superficie 50/50        Brier 0.2191   <- elegido
    + ranking 25%                      Brier 0.2165   <- FINAL
    K=250/(n+5)^0.4 (estilo 538)       Brier 0.2308   (descartado)

Calibracion del blend final: en la banda 0.50-0.60 predice 0.547 y la
frecuencia real fue 0.546. Acierto 64,5%.

NO se usa Monte Carlo. Se implemento un simulador punto-a-punto (saque y
resto de Sackmann) y NO bato a la media de la liga en total de juegos
(MAE 5.54 vs 5.26). El Elo solo predice el GANADOR; los totales exigen un
modelo de saque/resto por superficie que aun no demuestra skill.

Licencia del dato de entrada: CC BY-NC-SA 4.0 (Jeff Sackmann, via espejo).
"""
from __future__ import annotations

import math

INICIAL = 1500.0
K = 32.0                 # validado: mejor que el K dinamico de 538
PESO_SUPERFICIE = 0.5    # 50% Elo de la superficie + 50% Elo global
PESO_RANKING = 0.25      # 75% Elo + 25% ranking (mejor Brier)
B_RANKING = 0.5          # ajustado en <=2023
CORTE_POR_DEFECTO = "20240101"


def _prob(dif: float) -> float:
    """Probabilidad Elo de A frente a B a partir de la diferencia."""
    return 1.0 / (1.0 + 10 ** (-dif / 400.0))


def _efectivo(pid: str, superficie: str, elo: dict, selo: dict, sn: dict) -> float:
    """Elo que se usa para predecir: mezcla superficie/global.

    Si el jugador no tiene partidos en esa superficie, se usa el global
    (no se inventa un rating de superficie).
    """
    if sn.get((pid, superficie), 0) == 0:
        return elo.get(pid, INICIAL)
    return (PESO_SUPERFICIE * selo[(pid, superficie)]
            + (1 - PESO_SUPERFICIE) * elo.get(pid, INICIAL))


def actualizar(ganador: str, perdedor: str, pw: float,
               elo: dict, n: dict, selo: dict, sn: dict, superficie: str) -> None:
    """Actualiza los dos ratings con la probabilidad que habia ANTES."""
    d = K * (1.0 - pw)
    elo[ganador] = elo.get(ganador, INICIAL) + d
    elo[perdedor] = elo.get(perdedor, INICIAL) - d
    n[ganador] = n.get(ganador, 0) + 1
    n[perdedor] = n.get(perdedor, 0) + 1

    ego = selo.get((ganador, superficie), elo.get(ganador, INICIAL))
    epe = selo.get((perdedor, superficie), elo.get(perdedor, INICIAL))
    ps = _prob(ego - epe)
    ds = K * (1.0 - ps)
    selo[(ganador, superficie)] = ego + ds
    selo[(perdedor, superficie)] = epe - ds
    sn[(ganador, superficie)] = sn.get((ganador, superficie), 0) + 1
    sn[(perdedor, superficie)] = sn.get((perdedor, superficie), 0) + 1


def construir(partidos: list) -> dict:
    """Recorre los partidos EN ORDEN y devuelve ratings al final."""
    elo, n, selo, sn = {}, {}, {}, {}
    for f in partidos:
        w, l = str(f.get("winner_id") or ""), str(f.get("loser_id") or "")
        if not w or not l:
            continue
        sup = (f.get("surface") or "Unknown").strip() or "Unknown"
        pw = _prob(_efectivo(w, sup, elo, selo, sn) - _efectivo(l, sup, elo, selo, sn))
        actualizar(w, l, pw, elo, n, selo, sn, sup)
    return {"elo": elo, "n": n, "superficie": selo, "n_superficie": sn}


def prob_ranking(rank_a, rank_b, b: float = B_RANKING):
    """Probabilidad de A segun el ranking (None si falta alguno)."""
    try:
        ra, rb = float(rank_a), float(rank_b)
    except (TypeError, ValueError):
        return None
    if ra <= 0 or rb <= 0:
        return None
    return 1.0 / (1.0 + (ra / rb) ** b)


def predecir(modelo: dict, a: str, b: str, superficie: str = "Hard",
             rank_a=None, rank_b=None) -> dict:
    """Probabilidad final de que gane `a`.

    Blend validado: 75% Elo (superficie+global) + 25% ranking. Si no hay
    ranking, se devuelve solo el Elo (sin inventar el 25%).
    """
    elo, selo, sn = modelo["elo"], modelo["superficie"], modelo["n_superficie"]
    p_elo = _prob(_efectivo(a, superficie, elo, selo, sn)
                  - _efectivo(b, superficie, elo, selo, sn))
    p_rank = prob_ranking(rank_a, rank_b)
    if p_rank is None:
        return {"prob": p_elo, "prob_elo": p_elo, "prob_ranking": None,
                "fuente": "elo"}
    p = (1 - PESO_RANKING) * p_elo + PESO_RANKING * p_rank
    return {"prob": p, "prob_elo": p_elo, "prob_ranking": p_rank,
            "fuente": "elo+ranking"}


def _acum(d: dict, p: float) -> None:
    """Acumula Brier/log-loss/acierto desde la perspectiva del GANADOR."""
    d["n"] += 1
    d["brier"] += (p - 1.0) ** 2
    d["ll"] += -math.log(max(1e-9, p))
    d["ok"] += 1 if p > 0.5 else 0


def _resumen(d: dict) -> dict:
    n = d["n"]
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "brier": round(d["brier"] / n, 4),
        "logloss": round(d["ll"] / n, 4),
        "acierto": round(d["ok"] / n, 4),
    }


def backtest(partidos: list, corte: str = CORTE_POR_DEFECTO) -> dict:
    """Walk-forward: entrena con lo anterior a `corte` y mide despues.

    Devuelve metricas de Elo, ranking y blend, mas la tabla de calibracion
    del blend (probabilidad del favorito vs frecuencia real).
    """
    elo, n, selo, sn = {}, {}, {}, {}
    acc = {k: {"n": 0, "brier": 0.0, "ll": 0.0, "ok": 0}
           for k in ("elo", "ranking", "blend")}
    calib = {}

    for f in partidos:
        w, l = str(f.get("winner_id") or ""), str(f.get("loser_id") or "")
        if not w or not l:
            continue
        sup = (f.get("surface") or "Unknown").strip() or "Unknown"
        fecha = str(f.get("tourney_date") or "")

        p_elo = _prob(_efectivo(w, sup, elo, selo, sn)
                      - _efectivo(l, sup, elo, selo, sn))
        p_rank = prob_ranking(f.get("rank_ganador"), f.get("rank_perdedor"))

        if fecha >= corte:
            _acum(acc["elo"], p_elo)
            if p_rank is not None:
                _acum(acc["ranking"], p_rank)
                p = (1 - PESO_RANKING) * p_elo + PESO_RANKING * p_rank
                _acum(acc["blend"], p)
                pf = max(p, 1 - p)
                cubo = min(int(pf * 10), 9)
                c = calib.setdefault(cubo, [0, 0, 0.0])
                c[0] += 1
                c[1] += 1 if p > 0.5 else 0
                c[2] += pf

        actualizar(w, l, p_elo, elo, n, selo, sn, sup)

    tabla = []
    for cubo in sorted(calib):
        cnt, aciertos, suma = calib[cubo]
        if cnt < 30:
            continue
        tabla.append({
            "rango": f"{cubo / 10:.1f}-{(cubo + 1) / 10:.1f}",
            "n": cnt,
            "prob_media": round(suma / cnt, 3),
            "frecuencia_real": round(aciertos / cnt, 3),
        })

    return {
        "corte": corte,
        "modelos": {k: _resumen(v) for k, v in acc.items()},
        "calibracion": tabla,
        "moneda": {"brier": 0.25},
    }


def main(argv=None) -> int:
    """Backtest por consola:  python -m backend.fuentes.tennis.elo"""
    import argparse

    from . import sackmann

    ap = argparse.ArgumentParser(description="ELO tenis (Sackmann)")
    ap.add_argument("--tour", default="atp", choices=("atp", "wta"))
    ap.add_argument("--desde", type=int, default=2018)
    ap.add_argument("--hasta", type=int, default=None)
    ap.add_argument("--corte", default=CORTE_POR_DEFECTO)
    args = ap.parse_args(argv)

    print(f"[elo] cargando {args.tour.upper()} {args.desde}-{args.hasta or 'hoy'}...")
    partidos = sackmann.partidos(args.tour, args.desde, args.hasta)
    print(f"[elo] {len(partidos)} partidos")
    if not partidos:
        print("[elo] sin datos: revisa la cache o la conexion")
        return 1

    r = backtest(partidos, args.corte)
    print(f"\n== BACKTEST {args.tour.upper()} (out-of-sample desde {args.corte}) ==")
    for nombre, m in r["modelos"].items():
        if m.get("n"):
            print(f"  {nombre:8} n={m['n']:5} Brier={m['brier']:.4f} "
                  f"logloss={m['logloss']:.4f} acierto={m['acierto']:.1%}")
    print(f"  {'moneda':8} {'':10} Brier=0.2500 (referencia)")
    if r["calibracion"]:
        print("\n  calibracion del favorito:")
        for row in r["calibracion"]:
            print(f"    {row['rango']}  n={row['n']:5}  "
                  f"prob={row['prob_media']:.3f}  real={row['frecuencia_real']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

