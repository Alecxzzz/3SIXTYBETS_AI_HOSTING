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

import time

import cuotas_doradobet as book
import sports

from . import mercados, proveedores

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


def _valores(historial: dict, campo: str) -> list:
    """Valor del campo en cada partido (sin huecos)."""
    salida = []
    for partido in historial.get("partidos") or []:
        valor = (partido.get("stats") or {}).get(campo)
        if valor is not None:
            salida.append(valor)
    return salida


def _cumple(valores: list, linea: float, lado: str) -> int:
    """En cuantos partidos supero la linea (o se quedo por debajo, en under)."""
    if not valores:
        return 0
    if lado == "under":
        return sum(1 for v in valores if v <= linea)
    return sum(1 for v in valores if v >= linea)


def _fila(sport, prop, evento, historial, campo, n):
    """Fila final: la prop + su racha + el edge."""
    valores = _valores(historial, campo)[-n:]
    cumple = _cumple(valores, prop["linea"], prop["lado"])
    total = len(valores)
    pct = round(100.0 * cumple / total, 1) if total else None
    implicita = round(100.0 / prop["cuota"], 1) if prop.get("cuota") else None
    edge = (round(pct - implicita, 1)
            if pct is not None and implicita is not None else None)
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
        "valores": valores,
        "n_partidos": total,
        "cumple": cumple,
        "pct": pct,
        "edge": edge,
        "evento_id": evento.get("id"),
        "evento": evento.get("name"),
        "liga": evento.get("league"),
        "fecha_evento": evento.get("date"),
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


    # Mas valor primero; las que no tienen historico van al final.
    filas.sort(key=lambda f: (f["edge"] is None, -(f["edge"] or 0)))
    return {
        "ok": True,
        "props": filas,
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

    props, procesados, omitidos = [], 0, 0
    for evento in eventos:
        resultado = props_evento(sport, evento, n, max_jugadores=max_jugadores)
        if resultado.get("ok"):
            procesados += 1
            props.extend(resultado["props"])
        else:
            omitidos += 1

    props.sort(key=lambda f: (f["edge"] is None, -(f["edge"] or 0)))
    salida = {
        "ok": True,
        "sport": sport,
        "n": n,
        "eventos": len(eventos),
        "eventos_con_props": procesados,
        "eventos_sin_props": omitidos,
        "props": props,
        "actualizado": time.time(),
    }
    cache_set(clave, salida)
    return salida

