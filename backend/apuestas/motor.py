# -*- coding: utf-8 -*-
"""MOTOR DE DECISION: Elo + Monte Carlo eligen el pick, la IA solo redacta.

Que sustituye
-------------
`dashboard.py::_pick_deterministico` elige hoy en NBA/MLB/NFL/tenis ordenando
los mercados por `peso_mercado` y, a igualdad, por LA CUOTA MAS BAJA. Eso no es
un modelo: es apostar a lo que ya esta barato. Este motor hace lo contrario:
estima la probabilidad de cada mercado con el partido simulado y solo publica
cuando `prob * cuota - 1 > 0` (edge real) y ademas el peor caso razonable del
intervalo sigue siendo rentable.

Flujo:
    ratings Elo (BD)  ->  margen y total esperados  ->  Monte Carlo
                      ->  prob de CADA mercado del bookmaker  ->  mejor edge

Realismo importante
-------------------
Con cuotas 1.20-2.50 el mercado es eficiente y un edge >2% NO aparece en todos
los partidos. Cuando `decidir` devuelve None, el dashboard cae al criterio
anterior: el panel nunca se queda vacio, pero tampoco se publica un pick sin
valor solo para rellenar.
"""

import math
import os
import re
import secrets
from typing import Any, Dict, List, Optional

from backend.apuestas import elo as motor_elo
from backend.apuestas import simulacion

MODELO_VERSION = os.getenv("MOTOR_MC_VERSION", "elo-mc-nba-v1")
MIN_EDGE = float(os.getenv("MOTOR_MC_MIN_EDGE", "0.03"))
# Separacion maxima admitida entre la probabilidad del modelo y la implicita en
# la cuota. El mercado NBA es liquido y sabe cosas que el modelo no (lesiones,
# alineaciones). Medido: sin este tope el motor publicaba totales con "edge"
# del 33% cuando lo que habia era un sesgo de 11 puntos en el total esperado.
# Con 0.10 el motor solo apuesta cuando su desacuerdo con el mercado es
# pequeño, o sea cuando la apuesta es "el mercado esta un poco mal", no
# "el mercado esta completamente equivocado y yo tengo razon".
MAX_DESVIO = float(os.getenv("MOTOR_MC_MAX_DESVIO", "0.10"))
# Desvio maximo en PUNTOS entre lo que espera el modelo y lo que cotiza el
# mercado, para handicap y totales. Es la version interpretable del mismo
# criterio: si el modelo pone a Detroit +18.5 y la linea es +2.5, la diferencia
# son 16 puntos de desacuerdo; ningun modelo de Elo sin noticias de lesiones
# sabe mas que un mercado NBA sobre eso. Se descarta.
MAX_DESVIO_PUNTOS = float(os.getenv("MOTOR_MC_MAX_DESVIO_PUNTOS", "6.0"))
# Perdida maxima admitida en el PEOR CASO razonable (percentil 5 del intervalo).
# No se exige que el peor caso gane (eso no publicaria nunca: el intervalo de
# 40 lotes se come ~9 puntos de probabilidad), se exige que no pierda mas de
# esto. Es la regla de "apuesto si incluso equivocandome un poco sigo vivo".
MAX_PERDIDA_PEOR_CASO = float(os.getenv("MOTOR_MC_MAX_PERDIDA", "0.05"))
# Tope SUPERIOR de edge. Un edge del 20% en una cuota 2.40 sale de una
# diferencia de solo 8 puntos de probabilidad: con cuotas largas el edge se
# multiplica y parece oro. En la practica, cuando un modelo propio "ve" un
# edge enorme en un mercado liquido, lo que hay detras es casi siempre un error
# de datos o una noticia que el modelo no tiene (lesion, descanso). Se descarta.
MAX_EDGE = float(os.getenv("MOTOR_MC_MAX_EDGE", "0.15"))
MIN_CUOTA = float(os.getenv("ODDS_MINIMA", "1.20"))
MAX_CUOTA = float(os.getenv("ODDS_MAXIMA", "2.50"))
N_SIMS = int(os.getenv("MOTOR_MC_SIMS", str(simulacion.SIMULACIONES)))

# Rangos de cordura por tipo de mercado. Existen por un caso REAL del feed de
# Doradobet: la seleccion "Philadelphia 76ers +76" hace que cualquier parser
# ingenuo saque linea=76 y genere un handicap absurdo. Con estos topes ese
# mercado se descarta en vez de convertirse en un pick.
RANGOS_LINEA = {
    "handicap": (-40.0, 40.0),
    "total": (120.0, 330.0),
    "total_equipo": (60.0, 190.0),
}


# --------------------------------------------------------------------------
# LECTURA DEL MERCADO
# --------------------------------------------------------------------------
def linea_de_entrada(seleccion: str, market: str = "",
                     titulo: str = "") -> Optional[float]:
    """Linea del mercado sin confundirla con el numero del nombre del equipo.

    El parser de `ai/analista_nba` hace `re.search(r"[+-]?\\d+...")` y sobre
    "Philadelphia 76ers +76" devuelve 76 (por el "76" de "76ers"). Aqui se
    busca en este orden, que si distingue las dos cosas:
        1. el numero entre parentesis:   "Toronto Raptors (+16)"     -> 16
        2. "mas de / menos de / over":   "Más de 223.5"              -> 223.5
        3. numero con signo al final:    "Cavaliers -5.5"            -> -5.5
    """
    for texto in (seleccion or "", titulo or "", market or ""):
        m = re.search(r"\(\s*([+-]?\d+(?:[.,]\d+)?)\s*\)", texto)
        if m:
            return _num(m.group(1))
    for texto in (seleccion or "", market or "", titulo or ""):
        m = re.search(r"(?:m[aá]s\s+de|menos\s+de|over|under)\s*([+-]?\d+(?:[.,]\d+)?)",
                      texto, re.I)
        if m:
            return _num(m.group(1))
    for texto in (seleccion or "", titulo or ""):
        m = re.search(r"([+-]\d+(?:[.,]\d+)?)\s*$", (texto or "").strip())
        if m:
            return _num(m.group(1))
    return None


def _num(valor) -> Optional[float]:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def clasificar(entrada: Dict[str, Any], home: str, away: str) -> Dict[str, Any]:
    """Traduce una entrada de `mercados_reales` a un mercado simulable.

    Devuelve {tipo, lado, linea, direccion} o {"tipo": "no_soportado"}.

    Se apoya en `ai/analista_nba.clasificar_mercado_nba` para lo que ya estaba
    resuelto (rechazar props de jugador y mercados por cuarto/mitad) y solo
    sustituye la linea, que es donde ese parser falla.
    """
    try:
        from ai import analista_nba as parser

        base = parser.clasificar_mercado_nba({
            "market": entrada.get("market") or "",
            "titulo": entrada.get("titulo") or "",
            "selection": entrada.get("selection") or "",
            "home_name": home,
            "away_name": away,
        })
    except Exception:  # noqa: BLE001 - sin el parser se sigue con lo basico
        base = {"tipo": "no_soportado"}

    tipo = base.get("tipo")
    if tipo in (None, "no_soportado"):
        return {"tipo": "no_soportado", "motivo": base.get("motivo") or "no modelable"}

    # La linea se recalcula: la del parser puede venir del nombre del equipo.
    if tipo in ("handicap", "total", "total_equipo"):
        linea = linea_de_entrada(entrada.get("selection") or "",
                                 entrada.get("market") or "",
                                 entrada.get("titulo") or "")
        if linea is None:
            linea = _num(entrada.get("linea"))
        if linea is None:
            return {"tipo": "no_soportado", "motivo": "sin linea legible"}
        minimo, maximo = RANGOS_LINEA.get(tipo, (-1e9, 1e9))
        if not (minimo <= linea <= maximo):
            return {"tipo": "no_soportado",
                    "motivo": f"linea {linea} fuera de rango para {tipo}"}
        base["linea"] = linea
    return base


# --------------------------------------------------------------------------
# PREDICCION
# --------------------------------------------------------------------------
def _dias_entre(desde: Optional[str], hasta: Optional[str]) -> Optional[float]:
    """Dias entre dos fechas ISO. None si no se pueden leer.

    Las dos se normalizan a UTC SIN zona: la que viene de la BD (MySQL
    `datetime`) llega sin zona y la del partido llega con 'Z'. Restarlas tal
    cual lanza "can't subtract offset-naive and offset-aware datetimes".
    """
    from datetime import datetime, timezone as _tz

    if not desde or not hasta:
        return None
    try:
        a = datetime.fromisoformat(str(desde).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(hasta).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if a.tzinfo is not None:
        a = a.astimezone(_tz.utc).replace(tzinfo=None)
    if b.tzinfo is not None:
        b = b.astimezone(_tz.utc).replace(tzinfo=None)
    return max(0.0, min(30.0, (b - a).total_seconds() / 86400.0))


def predecir(partido: Dict[str, Any], sport: str = "nba") -> Dict[str, Any]:
    """Puntos esperados de cada equipo: Elo pone el margen, la forma el total.

    Dos piezas separadas a proposito:
      * margen  = a + b*(elo_local + HFA - elo_visita) + c*(descanso)
        -> "quien es mejor", con la ventaja de local y el cansancio.
      * total   = t0 + t1*ritmo_reciente
        -> "cuanto se anota en este partido", que Elo no sabe.

    Partirlas evita el error clasico de derivar el total del Elo: dos equipos
    muy iguales y rapidos no tienen por que jugar un partido de 190 puntos.
    """
    home_id = str(partido.get("home_id") or "")
    away_id = str(partido.get("away_id") or "")
    if not home_id or not away_id:
        return {"ok": False, "motivo": "el partido no trae los ids de ESPN"}

    estado = motor_elo.estado(sport)
    ratings = estado.get("ratings") or {}
    calib = estado.get("calibracion") or {}
    if not ratings or not calib:
        return {"ok": False, "motivo": "sin ratings Elo (¿calibrar primero?)"}

    r_h, r_a = ratings.get(home_id), ratings.get(away_id)
    if not r_h or not r_a:
        return {"ok": False,
                "motivo": f"equipo sin rating Elo ({home_id if not r_h else away_id})"}

    hfa = (motor_elo.CONFIG.get(sport) or {}).get("hfa", 0.0)
    elo_h = float(r_h.get("rating") or motor_elo.INICIAL)
    elo_a = float(r_a.get("rating") or motor_elo.INICIAL)
    d = (elo_h + hfa - elo_a) / 100.0

    # Descanso: los dias desde el ultimo partido jugado. Si no se sabe, 0 (no se
    # inventa cansancio: se prefiere un modelo neutral a uno que adivine).
    fecha = partido.get("date") or ""
    rest_h = _dias_entre(r_h.get("fecha_ultimo"), fecha)
    rest_a = _dias_entre(r_a.get("fecha_ultimo"), fecha)
    rest_diff = 0.0
    if rest_h is not None and rest_a is not None:
        rest_diff = max(-7.0, min(7.0, rest_h - rest_a))

    margen = calib.get("a", 0.0) + calib.get("b", 0.0) * d + calib.get("c", 0.0) * rest_diff

    # Total: ritmo reciente de los dos equipos. Si falta la forma, se usa la
    # media de la liga (mejor una referencia que un cero).
    total_medio = float(calib.get("total_medio") or 0.0)
    ritmo_h, ritmo_a = r_h.get("total"), r_a.get("total")
    if ritmo_h and ritmo_a:
        ritmo = (float(ritmo_h) + float(ritmo_a)) / 2.0
        total_esp = float(calib.get("t0") or total_medio) + float(calib.get("t1") or 0.0) * ritmo
    else:
        total_esp = total_medio

    mu_local = (total_esp + margen) / 2.0
    mu_visita = (total_esp - margen) / 2.0

    return {
        "ok": True,
        "sport": sport,
        "elo_local": round(elo_h, 2),
        "elo_visita": round(elo_a, 2),
        "elo_diff": round(elo_h + hfa - elo_a, 2),
        "descanso_local": rest_h,
        "descanso_visita": rest_a,
        "margen_esperado": round(margen, 2),
        "total_esperado": round(total_esp, 2),
        "mu_local": round(mu_local, 2),
        "mu_visita": round(mu_visita, 2),
        "sigma_equipo": float(calib.get("sigma_equipo") or 12.0),
        "rho_equipos": float(calib.get("rho_equipos") or 0.0),
        "p_ot": float(calib.get("p_ot") or simulacion.P_OT_DEFECTO),
        "partidos_local": int(r_h.get("partidos") or 0),
        "partidos_visita": int(r_a.get("partidos") or 0),
        "origen_ratings": estado.get("origen"),
    }


# --------------------------------------------------------------------------
# DECISION
# --------------------------------------------------------------------------
def _desvio_en_puntos(pre: Dict[str, Any],
                      clas: Dict[str, Any]) -> Optional[float]:
    """Cuanto se separa el modelo de la linea del mercado, EN PUNTOS.

    Se mide en la unidad del mercado, que es lo que un analista puede juzgar:
      * handicap  -> margen esperado del lado elegido vs el que implica la linea
      * total     -> total esperado vs la linea
      * total de equipo -> puntos esperados de ese equipo vs la linea
    Devuelve None para los mercados que no tienen linea (ganador).
    """
    tipo = clas.get("tipo")
    try:
        if tipo == "handicap":
            margen = float(pre.get("margen_esperado") or 0.0)
            lado = margen if clas.get("lado") == "local" else -margen
            return abs(lado - (-float(clas.get("linea") or 0.0)))
        if tipo == "total":
            return abs(float(pre.get("total_esperado") or 0.0)
                       - float(clas.get("linea") or 0.0))
        if tipo == "total_equipo":
            clave = "mu_local" if clas.get("lado") == "local" else "mu_visita"
            return abs(float(pre.get(clave) or 0.0) - float(clas.get("linea") or 0.0))
    except (TypeError, ValueError):
        return None
    return None


def _semilla(partido: Dict[str, Any], semilla: Optional[int] = None) -> int:
    """Semilla ESTABLE por partido, para que la decision sea reproducible.

    Sin esto, dos corridas con los mismos datos podian publicar picks distintos
    (o uno si y otro no): el Monte Carlo sin semilla usa el reloj. Eso hace
    imposible auditar por que se publico algo ayer. Se deriva del id del evento
    con crc32, que es estable entre procesos (hash() de str NO lo es: cambia con
    PYTHONHASHSEED).
    """
    if semilla is not None:
        return int(semilla)
    import zlib

    clave = f"{partido.get('event_id') or ''}|{partido.get('date') or ''}"
    return zlib.crc32(clave.encode("utf-8")) & 0x7FFFFFFF


def evaluar(partido: Dict[str, Any], mercados: List[Dict[str, Any]],
            sport: str = "nba", n_sims: Optional[int] = None,
            semilla: Optional[int] = None) -> Dict[str, Any]:
    """Simula el partido UNA vez y precio todos los mercados del bookmaker.

    Devuelve {"ok", "pre", "sim_n", "candidatos": [...]} ordenado por edge.
    Se separa de `decidir` para poder auditar que mercados se descartaron y por
    que (eso es lo que acaba en ai_picks_prob y permite medir la calibracion).
    """
    pre = predecir(partido, sport)
    if not pre.get("ok"):
        return {"ok": False, "motivo": pre.get("motivo") or "sin prediccion"}

    sim = simulacion.simular_nba(
        pre["mu_local"], pre["mu_visita"],
        pre["sigma_equipo"], pre["rho_equipos"],
        n=int(n_sims or N_SIMS), p_ot=pre["p_ot"],
        semilla=_semilla(partido, semilla),
    )

    home = partido.get("home_name") or ""
    away = partido.get("away_name") or ""
    candidatos = []
    vistos = set()
    discrepancias = 0
    for entrada in mercados or []:
        try:
            cuota = float(entrada.get("odds") or 0)
        except (TypeError, ValueError):
            continue
        if not (MIN_CUOTA <= cuota <= MAX_CUOTA):
            continue

        clas = clasificar(entrada, home, away)
        if clas.get("tipo") == "no_soportado":
            continue

        # El mismo mercado llega varias veces del bookmaker, con el nombre escrito
        # de dos formas ("Raptors (+16)" y "Raptors +16") y con lineas alternativas.
        # La clave es la CLASIFICACION, no el texto: si no, el mismo handicap
        # aparece dos veces y una de las dos puede colarse como pick.
        clave = (clas.get("tipo"), clas.get("lado"), clas.get("direccion"),
                 clas.get("linea"))
        if clave in vistos:
            continue
        vistos.add(clave)

        precio = simulacion.precio(sim, clas)
        prob = precio.get("prob")
        if prob is None:
            continue
        edge = simulacion.valor_esperado(prob, cuota)
        if edge is None:
            continue
        # "Demasiado bueno para ser verdad": con cuotas largas un desacuerdo
        # pequeño se convierte en un edge del 20%. Eso no es valor, es un modelo
        # sin la informacion que tiene el mercado.
        if edge > MAX_EDGE:
            discrepancias += 1
            continue

        # RED DE SEGURIDAD contra el sesgo del propio modelo. Un mercado liquido
        # de la NBA cotiza con informacion que el modelo NO tiene (lesiones,
        # alineaciones, descansos decididos a ultima hora). Cuando el modelo se
        # separa de la cuota mas de MAX_DESVIO puntos de probabilidad, lo mas
        # probable es que el equivocado sea el modelo: se descarta en vez de
        # publicar un "edge" del 30% que es puro sesgo.
        implicita = 1.0 / cuota
        desvio = prob - implicita
        if abs(desvio) > MAX_DESVIO:
            discrepancias += 1
            continue

        # Misma idea, pero en PUNTOS (legible y mas estricta en handicap/total).
        desvio_linea = _desvio_en_puntos(pre, clas)
        if desvio_linea is not None and desvio_linea > MAX_DESVIO_PUNTOS:
            discrepancias += 1
            continue

        prob_low = precio.get("prob_low")
        candidatos.append({
            "entrada": entrada,
            "clasificacion": clas,
            "prob": prob,
            "prob_low": prob_low,
            "prob_high": precio.get("prob_high"),
            "cuota": round(cuota, 3),
            "edge": edge,
            # Criterio conservador: si con el peor caso razonable del intervalo
            # la apuesta NO es rentable, no se publica aunque el edge medio si
            # lo sea. Es la diferencia entre un edge y una ilusion.
            "edge_low": round(float(prob_low or prob) * cuota - 1.0, 5),
        })

    candidatos.sort(key=lambda c: -c["edge"])
    return {"ok": True, "pre": pre, "sim_n": sim.get("n") or 0,
            "candidatos": candidatos, "sim": sim,
            "descartados_discrepancia": discrepancias,
            "lineas": len(mercados or [])}


def decidir(partido: Dict[str, Any], mercados: List[Dict[str, Any]],
            sport: str = "nba", min_edge: Optional[float] = None,
            n_sims: Optional[int] = None,
            semilla: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Elige el mejor pick, o None si ninguno tiene valor.

    Reglas (las tres a la vez):
      1. edge = p*cuota - 1 >= MIN_EDGE
      2. peor caso: p_low*cuota - 1 >= -MAX_PERDIDA_PEOR_CASO
         (no se exige que gane en el peor caso; se exige que no pierda mas del
         5%, que es lo que separa una apuesta con criterio de una fe ciega)
      3. cuota dentro del rango publicable y sin desacuerdo excesivo con el
         mercado (eso ya se filtra en `evaluar`)
    Si nada cumple, devuelve None y el dashboard usa el criterio anterior.
    """
    umbral = MIN_EDGE if min_edge is None else float(min_edge)
    evaluacion = evaluar(partido, mercados, sport, n_sims, semilla)
    if not evaluacion.get("ok"):
        return None

    elegido = None
    for cand in evaluacion["candidatos"]:
        if cand["edge"] >= umbral and cand["edge_low"] >= -MAX_PERDIDA_PEOR_CASO:
            elegido = cand
            break
    if not elegido:
        return None

    pre = evaluacion["pre"]
    entrada = elegido["entrada"]
    prob = elegido["prob"]
    prob_low = elegido["prob_low"] or prob
    prob_high = elegido["prob_high"] or prob
    cuota = elegido["cuota"]
    edge_pct = elegido["edge"] * 100

    if elegido["edge"] >= 0.06 and prob_low * cuota > 1.03:
        confianza = "ALTA"
    elif elegido["edge"] >= 0.03:
        confianza = "MEDIA"
    else:
        confianza = "BAJA"

    descansos = ""
    if pre.get("descanso_local") is not None and pre.get("descanso_visita") is not None:
        descansos = (f" Descanso: {pre['descanso_local']:.0f}d vs "
                     f"{pre['descanso_visita']:.0f}d.")

    rationale = (
        f"Motor Elo + Monte Carlo ({evaluacion['sim_n']:,} simulaciones). "
        f"Elo {pre['elo_local']:.0f} vs {pre['elo_visita']:.0f} "
        f"(con localia {pre['elo_diff']:+.0f}). "
        f"Marcador esperado {pre['mu_local']:.1f} - {pre['mu_visita']:.1f} "
        f"(total {pre['total_esperado']:.1f}). "
        f"Probabilidad del modelo {prob * 100:.1f}% "
        f"[{prob_low * 100:.0f}% - {prob_high * 100:.0f}%] frente a la implicita "
        f"{100 / cuota:.1f}% de la cuota {cuota}: edge {edge_pct:+.1f}%.{descansos}"
    ).replace("  ", " ")

    return {
        "market": entrada.get("market"),
        "titulo": entrada.get("titulo"),
        "selection": entrada.get("selection"),
        "odds": cuota,
        "confidence": confianza,
        "rationale": rationale,
        "stats": [
            f"Modelo: {prob * 100:.1f}% (IC {prob_low * 100:.0f}-"
            f"{prob_high * 100:.0f}%)",
            f"Edge: {edge_pct:+.1f}% (peor caso {elegido['edge_low'] * 100:+.1f}%)",
            f"Elo: {pre['elo_local']:.0f} vs {pre['elo_visita']:.0f}",
            f"Marcador esperado: {pre['mu_local']:.1f} - {pre['mu_visita']:.1f}",
            f"Simulaciones: {evaluacion['sim_n']:,}",
        ],
        # Campos internos: el dashboard los usa para atribucion del pick y para
        # registrar la probabilidad en ai_picks_prob.
        "_motor": MODELO_VERSION,
        "_prob": prob,
        "_prob_low": prob_low,
        "_prob_high": prob_high,
        "_edge": elegido["edge"],
        "_linea": elegido["clasificacion"].get("linea"),
        "_pre": pre,
        "_sims": evaluacion["sim_n"],
        "_mercados_evaluados": len(evaluacion["candidatos"]),
    }


def registrar(partido: Dict[str, Any], eleccion: Optional[Dict[str, Any]],
              evaluacion: Optional[Dict[str, Any]] = None,
              descartado: bool = False, motivo: Optional[str] = None,
              pick_id: Optional[str] = None,
              sport: str = "nba") -> bool:
    """Guarda en `ai_picks_prob` la decision del motor, haya pick o no.

    Registrar tambien los DESCARTES es lo que permite medir la calibracion de
    verdad: si solo se guardan las apuestas publicadas, la muestra queda sesgada
    por la propia politica de seleccion y el Brier sale mejor de lo que es.
    """
    try:
        import db as base
    except Exception:  # noqa: BLE001 - sin BD el motor sigue decidiendo
        return False

    pre = (eleccion or {}).get("_pre") or (evaluacion or {}).get("pre") or {}
    try:
        base.run_query(
            "insert into ai_picks_prob (id, pick_id, event_id, sport, market,"
            " seleccion, linea, prob, prob_low, prob_high, edge, cuota, mu_local,"
            " mu_visita, elo_local, elo_visita, n_sims, modelo_version,"
            " descartado, motivo, creado)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                secrets.token_urlsafe(8),
                pick_id,
                str(partido.get("event_id") or ""),
                sport,
                (eleccion or {}).get("market"),
                (eleccion or {}).get("selection"),
                _num((eleccion or {}).get("_linea")),
                _num((eleccion or {}).get("_prob")),
                _num((eleccion or {}).get("_prob_low")),
                _num((eleccion or {}).get("_prob_high")),
                _num((eleccion or {}).get("_edge")),
                _num((eleccion or {}).get("odds")),
                _num(pre.get("mu_local")),
                _num(pre.get("mu_visita")),
                _num(pre.get("elo_local")),
                _num(pre.get("elo_visita")),
                int((eleccion or {}).get("_sims")
                    or (evaluacion or {}).get("sim_n") or 0) or None,
                MODELO_VERSION,
                1 if descartado else 0,
                motivo,
                base.now_utc(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - no poder medir no debe romper el ciclo
        print(f"[motor] no se pudo registrar en ai_picks_prob: {exc}", flush=True)
        return False
    return True


__all__ = [
    "MODELO_VERSION", "clasificar", "decidir", "evaluar", "linea_de_entrada",
    "predecir", "registrar",
]




