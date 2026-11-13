# -*- coding: utf-8 -*-
"""MONTE CARLO sobre el partido: convierte (mu, sigma, rho) en probabilidades.

Por que Monte Carlo y no la formula cerrada
-------------------------------------------
La probabilidad de un mercado de NBA se puede escribir a mano cuando el mercado
es "gana el local" (una normal). Pero en cuanto se quiere el TOTAL, el total de
UN equipo, un handicap o dos mercados del mismo partido, la formula cerrada deja
de servir: hace falta simular el partido entero.

Lo que el Monte Carlo aporta aqui, concretamente:
  * Se simulan los DOS marcadores, no el margen. Asi el total, el margen y el
    total de un equipo salen del MISMO experimento y quedan correlacionados
    como en la realidad (un partido de ritmo alto sube los dos marcadores).
  * Intervalo de confianza: cada lote de simulaciones se hace con los puntos
    esperados PERTURBADOS. Eso mide la incertidumbre del PARAMETRO, que es la
    que importa (el error de muestreo de 20.000 tiradas es 0.003 y daria una
    falsa sensacion de precision).
  * Prorroga explicita, porque los mercados de Doradobet dicen literalmente
    "incl. prorroga" y el modelo Normal anterior la ignoraba por completo.

Monte Carlo NO añade informacion: si mu y sigma estan mal, devuelve un numero
preciso y equivocado. La informacion la pone Elo (backend/apuestas/elo.py).
"""

import random
from typing import Any, Dict, List, Optional

SIMULACIONES = 20000
LOTES = 40
# Desviacion de los puntos esperados (incertidumbre del PARAMETRO). No es el
# error de prediccion (que es ~11 puntos en NBA y lo mide la calibracion): es
# cuanto puede moverse la MEDIA estimada con datos nuevos. Con 3 temporadas de
# Elo y 15 partidos de forma, ~1.5 puntos.
# Se bajo de 3.0 porque con 3 el intervalo de 40 lotes se comia 0.19 de
# probabilidad y NINGUNA apuesta pasaba el filtro del peor caso: el motor se
# quedaba mudo no por prudencia sino por un error de escala.
SD_MU = 1.5
# Prorroga: se sobrescribe con la frecuencia REAL medida en la calibracion.
P_OT_DEFECTO = 0.065
OT_PUNTOS = 13.0
OT_SD = 5.0


def _percentil(valores: List[float], q: float) -> float:
    """Percentil por interpolacion lineal. Sin numpy."""
    if not valores:
        return 0.0
    orden = sorted(valores)
    if len(orden) == 1:
        return orden[0]
    pos = (len(orden) - 1) * max(0.0, min(1.0, q))
    bajo = int(pos)
    alto = min(bajo + 1, len(orden) - 1)
    frac = pos - bajo
    return orden[bajo] * (1 - frac) + orden[alto] * frac


def simular_nba(mu_local: float, mu_visita: float, sigma_equipo: float,
                rho_equipos: float = 0.0, n: int = SIMULACIONES,
                p_ot: float = P_OT_DEFECTO, sd_mu: float = SD_MU,
                semilla: Optional[int] = None) -> Dict[str, Any]:
    """Simula `n` partidos y devuelve los marcadores agrupados en lotes.

    Cada lote usa sus PROPIOS puntos esperados (mu + ruido), asi que la
    dispersion entre lotes es la incertidumbre del parametro. Los lotes son lo
    que da el intervalo de confianza de cada mercado.
    """
    rng = random.Random(semilla)
    lotes = max(1, min(LOTES, n))
    por_lote = max(1, n // lotes)
    rho = max(-0.9, min(0.9, float(rho_equipos or 0.0)))
    sigma = max(0.5, float(sigma_equipo or 1.0))

    salida = []
    for _ in range(lotes):
        mh = mu_local + (rng.gauss(0.0, sd_mu) if sd_mu > 0 else 0.0)
        ma = mu_visita + (rng.gauss(0.0, sd_mu) if sd_mu > 0 else 0.0)
        lote = []
        for _ in range(por_lote):
            z1 = rng.gauss(0.0, 1.0)
            # Segundo marcador correlacionado con el primero: los dos equipos
            # comparten ritmo (posesiones), asi que no son independientes.
            z2 = rho * z1 + (1.0 - rho * rho) ** 0.5 * rng.gauss(0.0, 1.0)
            pts_h = mh + sigma * z1
            pts_a = ma + sigma * z2
            if p_ot > 0 and rng.random() < p_ot:
                # La prorroga suma puntos al total. Se reparte al azar entre los
                # dos, que es lo que pasa en la realidad (el margen casi no se
                # mueve, el total sube).
                extra = rng.gauss(OT_PUNTOS, OT_SD)
                if rng.random() < 0.5:
                    pts_h += extra
                else:
                    pts_a += extra
            lote.append((pts_h, pts_a))
        salida.append(lote)

    return {"lotes": salida, "n": por_lote * lotes, "mu_local": mu_local,
            "mu_visita": mu_visita, "sigma_equipo": sigma, "rho_equipos": rho,
            "p_ot": p_ot}


def _cumple(pts_h: float, pts_a: float, clas: Dict[str, Any]) -> bool:
    """True si la simulacion cubre el mercado. Un solo sitio para la logica."""
    tipo = clas.get("tipo")
    margen_h = pts_h - pts_a
    total = pts_h + pts_a
    if tipo == "ganador":
        return margen_h > 0 if clas.get("lado") == "local" else margen_h < 0
    if tipo == "handicap":
        # La linea se aplica SOBRE el equipo elegido: +16 significa que le
        # valen perder por menos de 16.
        margen = margen_h if clas.get("lado") == "local" else -margen_h
        return margen + float(clas.get("linea") or 0.0) > 0
    if tipo == "total":
        linea = float(clas.get("linea") or 0.0)
        return total > linea if clas.get("direccion") == "over" else total < linea
    if tipo == "total_equipo":
        linea = float(clas.get("linea") or 0.0)
        valor = pts_h if clas.get("lado") == "local" else pts_a
        return valor > linea if clas.get("direccion") == "over" else valor < linea
    return False


def precio(sim: Dict[str, Any], clas: Dict[str, Any]) -> Dict[str, Any]:
    """Probabilidad del mercado + intervalo por lote.

    Devuelve prob (todas las simulaciones), prob_low/prob_high (percentiles 5 y
    95 ENTRE LOTES, o sea incertidumbre del parametro) y el numero de
    simulaciones que cubrieron el mercado.
    """
    lotes = sim.get("lotes") or []
    if not lotes or not clas or clas.get("tipo") in (None, "no_soportado"):
        return {"prob": None, "prob_low": None, "prob_high": None, "n": 0,
                "motivo": "mercado no simulado"}

    aciertos_total = 0
    simulaciones_total = 0
    por_lote = []
    for lote in lotes:
        if not lote:
            continue
        aciertos = sum(1 for ph, pa in lote if _cumple(ph, pa, clas))
        aciertos_total += aciertos
        simulaciones_total += len(lote)
        por_lote.append(aciertos / len(lote))

    if not simulaciones_total:
        return {"prob": None, "prob_low": None, "prob_high": None, "n": 0,
                "motivo": "sin simulaciones"}

    return {
        "prob": round(aciertos_total / simulaciones_total, 5),
        "prob_low": round(_percentil(por_lote, 0.05), 5),
        "prob_high": round(_percentil(por_lote, 0.95), 5),
        "n": simulaciones_total,
        "aciertos": aciertos_total,
    }


def valor_esperado(prob: float, cuota: float) -> Optional[float]:
    """EV por unidad apostada: p*cuota - 1. None si falta algun dato."""
    try:
        p = float(prob)
        o = float(cuota)
    except (TypeError, ValueError):
        return None
    if not (0.0 < p < 1.0) or o <= 1.0:
        return None
    return round(p * o - 1.0, 5)


__all__ = ["precio", "simular_nba", "valor_esperado", "SIMULACIONES"]

