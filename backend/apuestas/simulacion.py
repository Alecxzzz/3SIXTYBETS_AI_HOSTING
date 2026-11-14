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

import math
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

# --- Futbol -----------------------------------------------------------------
# Misma idea que en NBA pero por OTRA via: aqui los goles son 0, 1, 2, 3... y una
# normal metería probabilidad de -0.4 goles y de 2.7 goles, que no existen. Peor
# aun, el EMPATE es el resultado mas frecuente del futbol (~26%) y la normal lo
# trata como "margen exactamente 0", un suceso de probabilidad cero. Poisson
# modela el empate solo: goles_h == goles_a.
#
# La incertidumbre del parametro es mucho menor porque la escala es otra: 0.08
# goles de desviacion en la media (con la media en ~1.4) es ya un 6%.
SD_LAMBDA = 0.08
# Correlacion entre los goles de los dos equipos: un partido abierto sube los
# dos. Se implementa con un factor comun multiplicativo, que mantiene la
# marginal de cada equipo en Poisson.
RHO_FUTBOL = 0.25


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


def _poisson(lam: float, rng: random.Random) -> int:
    """Muestra Poisson por el algoritmo de Knuth.

    Vale para lambdas pequenas (0-6 goles), que es el rango del futbol. Para
    lambdas grandes el bucle se alarga y habria que usar otra via, pero aqui no
    se llega ni a 10.
    """
    if lam <= 0:
        return 0
    limite = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limite:
            return k
        k += 1
        if k > 60:  # imposible en futbol; evita un bucle infinito si lam es basura
            return k


def simular_futbol(mu_local: float, mu_visita: float,
                   rho_equipos: float = RHO_FUTBOL, n: int = SIMULACIONES,
                   sd_mu: float = SD_LAMBDA,
                   semilla: Optional[int] = None) -> Dict[str, Any]:
    """Simula `n` partidos de futbol: goles de cada equipo, en lotes.

    Devuelve el mismo contrato que `simular_nba` ({"lotes", "n", ...}) para que
    `precio()` y `_cumple()` sirvan sin cambios.

    La correlacion se mete como un FACTOR COMUN que multiplica las dos medias:
    `factor = exp(sigma*z - sigma^2/2)` tiene media 1, asi que cada equipo sigue
    siendo Poisson con su lambda y los dos suben o bajan juntos (partido abierto
    o cerrado). Es mas honesto que forzar una correlacion en el marcador, que en
    goles enteros no tiene sentido.
    """
    rng = random.Random(semilla)
    lotes = max(1, min(LOTES, n))
    por_lote = max(1, n // lotes)
    rho = max(0.0, min(0.9, float(rho_equipos or 0.0)))
    # sigma del factor comun: 0 sin correlacion, ~0.3 con rho 0.25 (medido en
    # la calibracion de NBA; en goles el efecto es el mismo concepto).
    sigma_comun = math.sqrt(rho) * 0.6 if rho > 0 else 0.0
    ajuste = math.exp(-(sigma_comun ** 2) / 2.0)

    salida = []
    for _ in range(lotes):
        mh = max(0.01, mu_local + (rng.gauss(0.0, sd_mu) if sd_mu > 0 else 0.0))
        ma = max(0.01, mu_visita + (rng.gauss(0.0, sd_mu) if sd_mu > 0 else 0.0))
        lote = []
        for _ in range(por_lote):
            if sigma_comun > 0:
                factor = math.exp(sigma_comun * rng.gauss(0.0, 1.0)) * ajuste
            else:
                factor = 1.0
            lote.append((float(_poisson(mh * factor, rng)),
                         float(_poisson(ma * factor, rng))))
        salida.append(lote)

    return {"lotes": salida, "n": por_lote * lotes, "mu_local": mu_local,
            "mu_visita": mu_visita, "sigma_equipo": 0.0, "rho_equipos": rho,
            "p_ot": 0.0, "deporte": "soccer"}


def simular(sport: str, mu_local: float, mu_visita: float,
            sigma_equipo: float = 0.0, rho_equipos: float = 0.0,
            n: int = SIMULACIONES, p_ot: float = P_OT_DEFECTO,
            semilla: Optional[int] = None) -> Dict[str, Any]:
    """Elige el simulador segun el deporte. Un solo punto de entrada.

    Sin esto, `motor.evaluar` tendria que saber de goles y de puntos a la vez y
    cada deporte nuevo obligaria a tocar el motor.
    """
    deporte = (sport or "").strip().lower()
    if deporte == "soccer" or deporte.startswith("soccer:"):
        return simular_futbol(mu_local, mu_visita, rho_equipos=rho_equipos,
                              n=n, semilla=semilla)
    return simular_nba(mu_local, mu_visita, sigma_equipo, rho_equipos, n=n,
                       p_ot=p_ot, semilla=semilla)


def _cumple(pts_h: float, pts_a: float, clas: Dict[str, Any]) -> bool:
    """True si la simulacion cubre el mercado. Un solo sitio para la logica."""
    tipo = clas.get("tipo")
    margen_h = pts_h - pts_a
    total = pts_h + pts_a
    if tipo == "ganador":
        return margen_h > 0 if clas.get("lado") == "local" else margen_h < 0
    if tipo == "resultado":
        # 1X2 del futbol: el empate es un resultado, no un margen de 0 puntos.
        lado = clas.get("lado")
        if lado == "local":
            return margen_h > 0
        if lado == "visita":
            return margen_h < 0
        return margen_h == 0
    if tipo == "doble_oportunidad":
        lado = clas.get("lado")
        if lado == "local_o_empate":
            return margen_h >= 0
        if lado == "visita_o_empate":
            return margen_h <= 0
        if lado == "local_o_visita":
            return margen_h != 0
        return False
    if tipo == "ambos_marcan":
        ambos = pts_h > 0 and pts_a > 0
        return ambos if clas.get("direccion") == "si" else (not ambos)
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


__all__ = ["precio", "simular", "simular_nba", "simular_futbol",
           "valor_esperado", "SIMULACIONES"]

