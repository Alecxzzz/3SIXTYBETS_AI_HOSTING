# -*- coding: utf-8 -*-
"""
MODELO CUANTITATIVO NBA (puntos, no goles).

Por que existe: el analista solo modelaba futbol (Poisson sobre goles). Con un
partido de NBA en el panel, `analizar_pick` devolvia "no modelable" y el boton
mostraba "Analizados: 0 / VALOR: 0" aunque hubiera picks generados. Aqui se usa
la MISMA base de ESPN que ya usa el dashboard para generar (forma reciente por
equipo, puntos a favor/en contra) y los promedios de temporada por jugador.

Diferencia clave con el futbol: un partido NBA son ~110 puntos por equipo. Un
Poisson con lambda=110 seria inmanejable, y la dispersion real es menor de la
que supone Poisson, asi que se modela con una NORMAL:

    margen  ~ Normal(esp_local - esp_visita, SD_MARGEN)
    total   ~ Normal(esp_local + esp_visita, SD_TOTAL)
    equipo  ~ Normal(esp_equipo, SD_EQUIPO)

Las desviaciones son las tipicas de la NBA (validables y conservadoras): un
equipo anota con sigma ~12-13 y el total del partido con sigma ~19.
"""
import math
import re
from typing import Any, Dict, List, Optional

# Desviaciones tipicas de la NBA (puntos). Conservadoras a proposito: si el
# modelo se equivoca, que sea por prudencia y no por exceso de confianza.
SD_EQUIPO = 12.5
SD_MARGEN = 13.5
SD_TOTAL = 19.0
VENTAJA_LOCAL = 3.0     # puntos que aporta jugar en casa

# sigma por estadistica de jugador (proporcional a su media si hay muestra)
_SD_PROP = {"pts": 0.30, "reb": 0.42, "ast": 0.45, "stl": 0.62, "blk": 0.66}
_STAT_PROP = {
    "puntos": "pts", "punto": "pts",
    "rebotes": "reb", "rebote": "reb",
    "asistencias": "ast", "asistencia": "ast",
    "robos": "stl", "robo": "stl",
    "tapones": "blk", "tapon": "blk",
}


def _media(valores: Optional[List[float]]) -> Optional[float]:
    vals = [v for v in (valores or []) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _cdf(z: float) -> float:
    """Normal estandar acumulada (sin scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def prob_sobre(media: float, sd: float, linea: float) -> float:
    """P(X > linea) para X ~ Normal(media, sd)."""
    if sd <= 0:
        return 1.0 if media > linea else 0.0
    return 1.0 - _cdf((linea - media) / sd)


def prob_bajo(media: float, sd: float, linea: float) -> float:
    """P(X < linea)."""
    return 1.0 - prob_sobre(media, sd, linea)


def puntos_esperados(forma_local: List[Dict[str, Any]],
                     forma_visita: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Puntos esperados de cada equipo a partir de su forma reciente (ESPN).

    Modelo multiplicativo clasico: la media implicita de la liga sale de la
    propia muestra, y cada equipo aporta su ataque y su defensa.

        esp_local = base * ataque_local * defensa_visita + ventaja/2
        esp_visita = base * ataque_visita * defensa_local - ventaja/2
    """
    pf_l = _media([g.get("gf") for g in forma_local])
    pa_l = _media([g.get("ga") for g in forma_local])
    pf_v = _media([g.get("gf") for g in forma_visita])
    pa_v = _media([g.get("ga") for g in forma_visita])

    if not all(v is not None for v in (pf_l, pa_l, pf_v, pa_v)):
        return {"ok": False, "motivo": "ESPN no devolvio puntos de ambos equipos"}

    base = (pf_l + pa_l + pf_v + pa_v) / 4.0
    if base <= 0:
        return {"ok": False, "motivo": "media de puntos invalida"}

    atk_l, def_l = pf_l / base, pa_l / base
    atk_v, def_v = pf_v / base, pa_v / base

    esp_l = base * atk_l * def_v + VENTAJA_LOCAL / 2.0
    esp_v = base * atk_v * def_l - VENTAJA_LOCAL / 2.0
    return {
        "ok": True,
        "base": round(base, 1),
        "local": round(esp_l, 1),
        "visita": round(esp_v, 1),
        "total": round(esp_l + esp_v, 1),
        "margen": round(esp_l - esp_v, 1),
        "pf_local": round(pf_l, 1), "pa_local": round(pa_l, 1),
        "pf_visita": round(pf_v, 1), "pa_visita": round(pa_v, 1),
        "n_local": len(forma_local or []),
        "n_visita": len(forma_visita or []),
    }


def _linea_en(texto: str) -> Optional[float]:
    """Primer numero con decimales o con signo del texto (-1.5, +4.5, 218.5)."""
    import re
    m = re.search(r"([+-]?\d+(?:[.,]\d+)?)", texto or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _menciona(texto: str, nombre: str) -> bool:
    """True si el texto nombra al equipo (por la ultima palabra significativa)."""
    if not texto or not nombre:
        return False
    partes = [p for p in nombre.lower().replace(".", " ").split() if len(p) > 3]
    if not partes:
        return False
    return partes[-1] in texto.lower()



def clasificar_mercado_nba(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Traduce el pick a un tipo de mercado NBA con su lado y su linea."""
    market = (pick.get("market") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    seleccion = pick.get("selection") or ""
    sel_low = seleccion.lower()
    txt = f"{market} {titulo}"
    todo = f"{txt} {sel_low}"
    home = pick.get("home_name") or ""
    away = pick.get("away_name") or ""

    # Los mercados POR PARTES (cuarto, mitad) no estan en el modelo: se
    # resolverian con el total del partido, que es otro mercado. Se rechazan.
    if re.search(r"\b(primer|1er|1º|segundo|2do|2º|tercer|3er|3º|cuarto|4to|4º|mitad)\b", txt) \
            and ("cuarto" in txt or "mitad" in txt or "half" in txt):
        return {"tipo": "no_soportado",
                "motivo": "mercados por cuarto o mitad no estan en el modelo NBA"}

    # ---- PROPS de jugador ("Minimo de puntos del jugador") ----
    if "minimo" in txt or "mínimo" in txt or "min." in txt:
        stat = None
        for palabra, clave in _STAT_PROP.items():
            if palabra in txt:
                stat = clave
                break
        if stat is None:
            return {"tipo": "no_soportado",
                    "motivo": "prop de una estadistica sin dato gratis (triples/PRA)"}
        linea = _linea_en(seleccion) or _linea_en(titulo)
        if linea is None:
            return {"tipo": "no_soportado", "motivo": "el prop no trae linea"}
        return {"tipo": "prop", "stat": stat, "linea": linea,
                "jugador": seleccion, "texto": todo}

    # ---- GANADOR ----
    if "ganador" in txt or "moneyline" in txt or "1x2" in txt:
        lado = "local" if _menciona(f"{seleccion} {market}", home) else (
            "visita" if _menciona(f"{seleccion} {market}", away) else None)
        if lado is None:
            return {"tipo": "no_soportado", "motivo": "no se identifica el equipo elegido"}
        return {"tipo": "ganador", "lado": lado}

    # ---- HANDICAP ----
    if "handicap" in txt or "hándicap" in txt or "spread" in txt:
        linea = _linea_en(seleccion) or _linea_en(titulo)
        if linea is None:
            return {"tipo": "no_soportado", "motivo": "handicap sin linea"}
        lado = "local" if _menciona(f"{seleccion} {market}", home) else (
            "visita" if _menciona(f"{seleccion} {market}", away) else None)
        if lado is None:
            return {"tipo": "no_soportado", "motivo": "no se identifica el equipo elegido"}
        return {"tipo": "handicap", "lado": lado, "linea": linea}

    # ---- TOTALES (del partido o de un equipo) ----
    if "total" in txt or "totales" in txt or "puntos" in txt:
        linea = _linea_en(seleccion) or _linea_en(titulo)
        if linea is None:
            return {"tipo": "no_soportado", "motivo": "total sin linea"}
        direccion = "over" if ("over" in todo or "mas de" in todo or "más de" in todo) else (
            "under" if ("under" in todo or "menos de" in todo) else None)
        if direccion is None:
            return {"tipo": "no_soportado", "motivo": "total sin over/under"}
        if _menciona(txt, home):
            return {"tipo": "total_equipo", "lado": "local", "linea": linea,
                    "direccion": direccion}
        if _menciona(txt, away):
            return {"tipo": "total_equipo", "lado": "visita", "linea": linea,
                    "direccion": direccion}
        return {"tipo": "total", "linea": linea, "direccion": direccion}

    return {"tipo": "no_soportado", "motivo": "mercado NBA no modelable con datos de ESPN"}


def _promedio_jugador(jugador: str, promedios: List[Dict[str, Any]],
                      stat: str) -> Optional[Dict[str, Any]]:
    """Busca al jugador en los promedios de temporada de ESPN y da su media."""
    if not jugador or not promedios:
        return None
    nombre = jugador.lower().split("(")[0]
    for p in promedios:
        corto = (p.get("corto") or "").lower()
        largo = (p.get("nombre") or "").lower()
        apellido = largo.split()[-1] if largo else ""
        if (largo and largo in nombre) or (corto and corto in nombre) or \
                (apellido and len(apellido) > 3 and apellido in nombre):
            valor = p.get(stat)
            if valor is None:
                return None
            return {"jugador": p.get("nombre") or p.get("corto"),
                    "media": float(valor),
                    "partidos": int(p.get("partidos") or 0),
                    "minutos": p.get("min")}
    return None


def analizar_pick_nba(pick: Dict[str, Any], forma_local: List[Dict[str, Any]],
                      forma_visita: List[Dict[str, Any]],
                      promedios_local: Optional[List[Dict[str, Any]]] = None,
                      promedios_visita: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Analisis cuantitativo de un pick NBA. Mismo contrato que analizar_pick."""
    home = pick.get("home_name") or ""
    away = pick.get("away_name") or ""
    mercado = clasificar_mercado_nba(pick)

    base: Dict[str, Any] = {
        "ok": True,
        "pick_id": pick.get("id"),
        "deporte": "nba",
        "partido": f"{home} vs {away}",
        "mercado_detectado": mercado.get("tipo"),
        "lado": mercado.get("lado"),
        "linea": mercado.get("linea"),
        "forma_local": forma_local,
        "forma_visita": forma_visita,
    }

    if mercado.get("tipo") == "no_soportado":
        base["soportado"] = False
        base["motivo"] = mercado.get("motivo")
        return base

    esp = puntos_esperados(forma_local, forma_visita)
    if not esp.get("ok"):
        base["ok"] = False
        base["motivo"] = esp.get("motivo")
        return base

    base["fuerzas"] = {"n_local": esp["n_local"], "n_visita": esp["n_visita"]}
    base["goles_esperados"] = {"local": esp["local"], "visita": esp["visita"]}
    base["puntos_esperados"] = {
        "local": esp["local"], "visita": esp["visita"], "total": esp["total"],
        "margen": esp["margen"], "base_liga": esp["base"],
    }

    prob = None

    # ---- GANADOR (incluye prorroga: decide el margen final) ----
    if mercado["tipo"] == "ganador":
        p_local = prob_sobre(esp["margen"], SD_MARGEN, 0.0)
        prob = p_local if mercado["lado"] == "local" else 1.0 - p_local

    # ---- HANDICAP: P(margen_del_lado + linea > 0) ----
    elif mercado["tipo"] == "handicap":
        margen_lado = esp["margen"] if mercado["lado"] == "local" else -esp["margen"]
        prob = prob_sobre(margen_lado, SD_MARGEN, -mercado["linea"])

    # ---- TOTAL DEL PARTIDO ----
    elif mercado["tipo"] == "total":
        if mercado["direccion"] == "over":
            prob = prob_sobre(esp["total"], SD_TOTAL, mercado["linea"])
        else:
            prob = prob_bajo(esp["total"], SD_TOTAL, mercado["linea"])

    # ---- TOTAL DE UN EQUIPO ----
    elif mercado["tipo"] == "total_equipo":
        media = esp["local"] if mercado["lado"] == "local" else esp["visita"]
        if mercado["direccion"] == "over":
            prob = prob_sobre(media, SD_EQUIPO, mercado["linea"])
        else:
            prob = prob_bajo(media, SD_EQUIPO, mercado["linea"])

    # ---- PROPS DE JUGADOR ----
    elif mercado["tipo"] == "prop":
        jugador = _promedio_jugador(mercado["jugador"], promedios_local, mercado["stat"]) \
            or _promedio_jugador(mercado["jugador"], promedios_visita, mercado["stat"])
        if not jugador:
            base["soportado"] = False
            base["motivo"] = ("no se encontro al jugador en los promedios de ESPN "
                              "(o el mercado pide una estadistica sin dato gratis)")
            return base
        sd = max(0.8, jugador["media"] * _SD_PROP.get(mercado["stat"], 0.35))
        prob = prob_sobre(jugador["media"], sd, mercado["linea"])
        base["jugador"] = jugador
        base["limitaciones"] = [
            f"Promedios de temporada de ESPN ({jugador['partidos']} partidos).",
            "No descuenta lesiones, minutos ni el rival de esta noche.",
            "La dispersion se estima desde la media: es una aproximacion.",
        ]

    if prob is None:
        base["soportado"] = False
        base["motivo"] = "no se pudo calcular la probabilidad del mercado"
        return base

    prob = max(0.01, min(0.99, float(prob)))
    base["soportado"] = True
    base["distribucion"] = {
        "prob": round(prob, 4),
        "puntos_esperados": esp["total"],
        "margen_esperado": esp["margen"],
    }
    base.setdefault("limitaciones", [
        "Modelo normal sobre los ultimos partidos de ESPN (puntos a favor/en contra).",
        "No conoce lesiones, descansos ni cambios de alineacion de ultima hora.",
        "La ventaja de local se fija en 3 puntos; no distingue back-to-backs.",
    ])
    return base

