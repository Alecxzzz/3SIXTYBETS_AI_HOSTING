# -*- coding: utf-8 -*-
"""
Servicio del apartado de props de jugador.

Une las dos piezas:
    mercados.py     -> que publica el bookmaker (jugador, prop, linea, cuota)
    proveedores.py  -> que hizo ese jugador en sus ultimos N partidos

y calcula lo unico que el usuario necesita para juzgar una cuota:
    valores[]   lo que hizo en cada partido (altura de la barra)
    cumple/n    en cuantos de esos partidos supero la linea (racha)
    pct         ese porcentaje
    implicita   1/cuota (lo que el bookmaker le asigna)
    edge        pct - implicita (si > 0 la cuota paga de mas)

Nada de esto decide ni publica picks: es informacion.
"""

import os
import time

import cuotas_doradobet as book
import db
import sports

from . import mercados, proveedores

# Solo se publican props que el jugador haya cumplido al menos este % de las
# veces en la ventana. Por debajo es ruido: la muestra es de 5 partidos y un
# 1/5 (20%) no dice nada util.
PROPS_PCT_MINIMA = float(os.getenv("PROPS_PCT_MINIMA", "60"))

# Cache en memoria del resultado por (deporte, evento, n). La pagina y el
# scheduler leen de aqui; el scheduler ademas lo persiste en la BD.
TTL_S = 15 * 60
_CACHE = {}
_MAX_CACHE = 400


def cache_get(clave):
    item = _CACHE.get(clave)
    if not item:
        return None
    ts, valor = item
    if time.time() - ts > TTL_S:
        _CACHE.pop(clave, None)
        return None
    return valor


def cache_set(clave, valor):
    if len(_CACHE) > _MAX_CACHE:
        _CACHE.clear()
    _CACHE[clave] = (time.time(), valor)


def cache_clear():
    _CACHE.clear()


# ------------------------------------------------------------- calculo


def _detalle(historial: dict, campo: str, n: int) -> list:
    """Los ultimos n partidos con su valor, su fecha y el rival."""
    filas = []
    for partido in historial.get("partidos") or []:
        valor = (partido.get("stats") or {}).get(campo)
        if valor is None:
            continue
        filas.append({
            "valor": valor,
            "fecha": partido.get("fecha"),
            "rival": partido.get("rival"),
            "casa": partido.get("casa"),
        })
    return filas[-n:]


def _cumple(valores: list, linea: float, lado: str) -> int:
    """En cuantos partidos supero la linea (o se quedo por debajo, en under)."""
    if not valores:
        return 0
    if lado == "under":
        return sum(1 for v in valores if v <= linea)
    return sum(1 for v in valores if v >= linea)


def _wilson(cumple: int, total: int, z: float = 1.96):
    """Limite INFERIOR del intervalo de confianza (Wilson) del porcentaje.

    Con 5 partidos, un 5/5 da 100% pero el limite inferior es ~57%: es la
    forma honesta de no vender como certeza lo que puede ser suerte. Se usa
    para el "edge ajustado", que es el que deberia mirar el usuario.
    """
    if not total:
        return None
    p = cumple / total
    denom = 1 + (z * z) / total
    centro = p + (z * z) / (2 * total)
    margen = z * (((p * (1 - p)) / total + (z * z) / (4 * total * total)) ** 0.5)
    return max(0.0, min(1.0, (centro - margen) / denom))


def _fila(sport, prop, evento, historial, campo, n):
    """Fila final: la prop + su racha + cuota justa + edge (crudo y ajustado)."""
    detalle = _detalle(historial, campo, n)
    valores = [d["valor"] for d in detalle]
    total = len(valores)
    cumple = _cumple(valores, prop["linea"], prop["lado"])
    pct = round(100.0 * cumple / total, 1) if total else None

    bajo = _wilson(cumple, total)
    pct_ajustado = round(100.0 * bajo, 1) if bajo is not None else None

    implicita = round(100.0 / prop["cuota"], 1) if prop.get("cuota") else None
    edge = (round(pct - implicita, 1)
            if pct is not None and implicita is not None else None)
    edge_ajustado = (round(pct_ajustado - implicita, 1)
                     if pct_ajustado is not None and implicita is not None else None)
    # Cuota que DEBERIA pagar si el porcentaje real fuera su probabilidad.
    cuota_justa = round(100.0 / pct, 2) if pct else None

    return {
        "sport": sport,
        "jugador": prop["jugador"],
        "equipo": prop.get("equipo"),
        "foto": historial.get("foto"),
        "jugador_id": historial.get("id"),
        "prop": prop["prop"],
        "prop_raw": prop.get("prop_raw"),
        "lado": prop["lado"],
        "linea": prop["linea"],
        "cuota": prop["cuota"],
        "implicita": implicita,
        "cuota_justa": cuota_justa,
        "detalle": detalle,
        "valores": valores,
        "n_partidos": total,
        "cumple": cumple,
        "pct": pct,
        "pct_ajustado": pct_ajustado,
        "edge": edge,
        "edge_ajustado": edge_ajustado,
        "edge_mostrado": edge,
        "evento_id": evento.get("id"),
        "evento": evento.get("name"),
        "liga": evento.get("league"),
        "liga_code": evento.get("league_code"),
        "fecha_evento": evento.get("date"),
        "estado": "PENDIENTE",
        "fuente": "statsapi" if sport == "mlb" else "espn",
    }


# ------------------------------------------------------------- API publica


def props_evento(sport: str, evento: dict, n: int = 5,
                 max_jugadores: int = 12) -> dict:
    """Props de jugador de UN evento, con la racha real de cada jugador.

    `max_jugadores` acota el coste: un partido de futbol publica props de ~78
    jugadores y cada uno exige una llamada de historico. Se prioriza a los
    jugadores con MAS mercados publicados (los titulares) y, a igualdad, al
    de menor cuota.
    """
    inicio = time.time()
    home = (evento.get("home") or {}).get("name")
    away = (evento.get("away") or {}).get("name")
    detalle = book.detalle_de_evento(sport, home, away, evento.get("date"))
    if not detalle:
        return {"ok": False, "props": [],
                "motivo": "el bookmaker no publica este evento"}

    campos = mercados.STATS.get(sport) or {}
    elegidas = mercados.elegir_principal(mercados.props_de_detalle(sport, detalle))

    por_jugador = {}
    for prop in elegidas:
        if prop["prop"] not in campos:
            continue
        por_jugador.setdefault(
            (prop["jugador"].lower(), prop.get("equipo") or ""), []
        ).append(prop)

    # Reparto EQUILIBRADO por equipo: si se ordenara solo por "mas mercados",
    # un equipo podia copar la lista entera y el apartado parecia de un solo
    # lado del partido. Se alterna equipo A / equipo B.
    por_equipo = {}
    for (nombre, equipo), lista in por_jugador.items():
        por_equipo.setdefault(equipo, []).append(lista)
    for lista in por_equipo.values():
        lista.sort(key=lambda p: (-len(p), min(x["cuota"] for x in p)))
    ordenados, equipos = [], list(por_equipo.values())
    while len(ordenados) < max_jugadores and any(equipos):
        for lista in equipos:
            if lista and len(ordenados) < max_jugadores:
                ordenados.append(lista.pop(0))
        equipos = [e for e in equipos if e]


    historiales, filas = {}, []
    for lista in ordenados:
        for prop in lista:
            campo = campos.get(prop["prop"])
            if not campo:
                continue
            clave = (prop["jugador"].lower(), prop.get("equipo") or "")
            if clave not in historiales:
                historiales[clave] = proveedores.historial_jugador(
                    sport, prop["jugador"], prop.get("equipo"), evento, n
                )
            historial = historiales[clave]
            if not historial.get("encontrado"):
                continue
            filas.append(_fila(sport, prop, evento, historial, campo, n))


    # FILTRO: solo props cumplidas al menos PROPS_PCT_MINIMA (60% por defecto).
    # Lo demas es ruido con una muestra de 5 partidos.
    descartadas = sum(
        1 for f in filas if f["pct"] is None or f["pct"] < PROPS_PCT_MINIMA
    )
    filas = [f for f in filas
             if f["pct"] is not None and f["pct"] >= PROPS_PCT_MINIMA]
    filas.sort(key=lambda f: (f["edge"] is None, -(f["edge"] or 0)))
    return {
        "ok": True,
        "props": filas,
        "pct_minima": PROPS_PCT_MINIMA,
        "descartadas_por_pct": descartadas,
        "jugadores_sin_historico": sum(
            1 for h in historiales.values() if not h.get("encontrado")),
        "ms": int((time.time() - inicio) * 1000),
    }


def props_deporte(sport: str, n: int = 5, limite_eventos: int = 15,
                  solo_pendientes: bool = True, usar_cache: bool = True,
                  max_jugadores: int = 12) -> dict:
    """Props de todos los partidos del deporte (hoy, aun no empezados).

    `solo_pendientes` descarta los partidos ya jugados o en juego: un prop de
    un partido terminado no se puede apostar y solo ensuciaria la lista.
    """
    if sport not in mercados.DEPORTES:
        return {"ok": False, "props": [], "motivo": f"deporte no soportado: {sport}"}

    clave = f"deporte:{sport}:{n}:{limite_eventos}:{solo_pendientes}:{max_jugadores}"
    if usar_cache:
        cacheado = cache_get(clave)
        if cacheado is not None:
            return cacheado

    data = sports.get_sport_games(sport)
    eventos = list(data.get("games") or [])
    if solo_pendientes:
        eventos = [e for e in eventos if (e.get("state") or "pre") == "pre"]
    eventos.sort(key=lambda e: e.get("date") or "")
    eventos = eventos[:limite_eventos]

    props, procesados, omitidos, descartadas = [], 0, 0, 0
    # En PARALELO: cada evento es una llamada HTTP al bookmaker (~2-8 s). En
    # serie, 6 eventos por deporte tardaban medio minuto o mas por ciclo.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(
            lambda ev: props_evento(sport, ev, n, max_jugadores=max_jugadores),
            eventos,
        ))

    for resultado in resultados:
        if resultado.get("ok"):
            procesados += 1
            props.extend(resultado["props"])
            descartadas += resultado.get("descartadas_por_pct") or 0
        else:
            omitidos += 1

    # Mismo filtro de calidad que props_evento (por si algun evento se salto).
    props = [f for f in props if f.get("pct") is not None
             and f["pct"] >= PROPS_PCT_MINIMA]
    props.sort(key=lambda f: (f["edge"] is None, -(f["edge"] or 0)))
    salida = {
        "ok": True,
        "sport": sport,
        "n": n,
        "pct_minima": PROPS_PCT_MINIMA,
        "descartadas_por_pct": descartadas,
        "eventos": len(eventos),
        "eventos_con_props": procesados,
        "eventos_sin_props": omitidos,
        "props": props,
        "actualizado": time.time(),
    }
    cache_set(clave, salida)
    return salida



# ------------------------------------------------------- cierre (A1)


def cerrar_props(limite: int = 400) -> dict:
    """Cierra las props de partidos ya jugados: ACIERTO / FALLO.

    Para cada jugador se pide su historico UNA sola vez y se busca el partido
    del evento por fecha. Si ese partido todavia no aparece en la fuente, la
    prop se queda PENDIENTE para el siguiente ciclo: no se inventa veredicto.

    Es lo que convierte el apartado en algo demostrable: con el veredicto
    guardado se puede medir la efectividad real (db.track_record_player_props).
    """
    filas = db.list_player_props_por_cerrar(limite)
    if not filas:
        return {"revisadas": 0, "cerradas": 0, "sin_partido": 0}

    grupos = {}
    for f in filas:
        clave = (f.get("sport"), f.get("league_code"), str(f.get("player_espn_id")))
        grupos.setdefault(clave, []).append(f)

    resultados, sin_partido = [], 0
    for (sport, league, player_id), props in grupos.items():
        try:
            partidos = proveedores.partidos_jugador(sport, league, player_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[props] cierre: historico de {player_id} fallo: {exc}", flush=True)
            partidos = []
        por_fecha = {(p.get("fecha") or "")[:10]: p for p in partidos}

        for f in props:
            fecha = str(f.get("event_date") or "")[:10]
            partido = por_fecha.get(fecha)
            if not partido:
                sin_partido += 1
                continue
            campo = (mercados.STATS.get(sport) or {}).get(f.get("prop"))
            if not campo:
                continue
            valor = (partido.get("stats") or {}).get(campo)
            if valor is None:
                sin_partido += 1
                continue
            linea = float(f.get("line_value") or 0)
            lado = f.get("side") or "over"
            cumple = (valor <= linea) if lado == "under" else (valor >= linea)
            resultados.append({
                "id": f["id"],
                "estado": "ACIERTO" if cumple else "FALLO",
                "valor_real": float(valor),
            })

    cerradas = db.cerrar_player_props(resultados)
    return {
        "revisadas": len(filas),
        "cerradas": cerradas,
        "sin_partido": sin_partido,
    }

