# -*- coding: utf-8 -*-
"""Modelo de TENIS: de jugadores a probabilidad de ganar el partido.

Es la capa que usara el generador de picks. NO decide si apostar (eso lo hace
el motor con la cuota); solo responde "que probabilidad tiene A de ganar a B".

    prob = 0.75 * Elo(partido) + 0.25 * ranking      (Brier 0.2168)

Si alguno de los dos jugadores no esta en los datos historicos, devuelve None:
no se inventa una probabilidad para alguien que el modelo no ha visto nunca
(mismo criterio que el resto del proyecto: si no hay evidencia, no se publica).

Licencia del dato de entrada: CC BY-NC-SA 4.0 (Jeff Sackmann, via espejo).
"""
from __future__ import annotations

import unicodedata

from . import elo as motor_elo
from . import sackmann

# Cache en proceso: construir el motor recorre ~20 anos de partidos.
_MOTOR_CACHE: dict = {}


def _norm(nombre: str) -> str:
    """Nombre comparable: minusculas, sin acentos ni puntuacion."""
    s = unicodedata.normalize("NFKD", (nombre or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace(".", " ").replace(",", " ")
    return " ".join(s.split())


def cargar(tour: str = "atp", desde: int = 2018, hasta: int | None = None,
           refrescar: bool = False) -> dict:
    """Construye (y cachea) el motor con los ratings actuales."""
    clave = (tour.lower(), int(desde), hasta)
    if _MOTOR_CACHE.get("clave") == clave and not refrescar:
        return _MOTOR_CACHE["motor"]

    partidos = sackmann.partidos(tour, desde, hasta)
    motor = motor_elo.construir(partidos)
    motor["tour"] = tour.lower()

    # Indice nombre -> id para poder emparejar los nombres del pick
    nombres: dict = {}
    for f in partidos:
        for id_, nombre in ((f.get("winner_id"), f.get("winner_name")),
                            (f.get("loser_id"), f.get("loser_name"))):
            if id_ and nombre:
                nombres.setdefault(_norm(nombre), str(id_))
    motor["nombres"] = nombres
    motor["partidos_total"] = len(partidos)

    _MOTOR_CACHE["clave"] = clave
    _MOTOR_CACHE["motor"] = motor
    return motor


def buscar_id(motor: dict, nombre: str):
    """Id del jugador segun su nombre, o None si no aparece en el historico."""
    n = _norm(nombre)
    if not n:
        return None
    nombres = motor.get("nombres") or {}
    if n in nombres:
        return nombres[n]
    # "Zverev A." / "A. Zverev" -> probar por apellido unico
    partes = [p for p in n.split() if len(p) >= 4]
    for p in partes:
        candidatos = [pid for nom, pid in nombres.items() if p in nom.split()]
        if len(candidatos) == 1:
            return candidatos[0]
    return None


def predecir(motor: dict, nombre_a: str, nombre_b: str,
             superficie: str = "Hard", rank_a=None, rank_b=None) -> dict | None:
    """Probabilidad de que A gane a B.

    Devuelve ``{"prob", "prob_elo", "prob_ranking", "id_a", "id_b"}`` o None si
    alguno de los dos jugadores no existe en el historico.
    """
    id_a = buscar_id(motor, nombre_a)
    id_b = buscar_id(motor, nombre_b)
    if not id_a or not id_b or id_a == id_b:
        return None

    sup = (superficie or "Hard").strip() or "Hard"
    # La formula vive en elo.predecir (una sola fuente de verdad).
    r = motor_elo.predecir(motor, id_a, id_b, sup, rank_a, rank_b)
    r.update({"id_a": id_a, "id_b": id_b, "superficie": sup,
              "nombre_a": nombre_a, "nombre_b": nombre_b})
    return r


def guardar_en_bd(motor: dict, tour: str = "atp", lote: int = 200) -> int:
    """Persiste los ratings en `elo_ratings` (sport = tennis:<tour>).

    Reutiliza la tabla del motor Elo/MC: el panel y el backtest leen los mismos
    ratings sin duplicar esquema.

    Se hace por LOTES: 749 INSERT uno a uno contra Aiven (SSL, ~300 ms cada
    uno) tardaban minutos y bloqueaban el proceso. Con chunks de 200 son 4
    consultas.
    """
    import db

    sport = f"tennis:{tour.lower()}"
    filas = [
        (sport, str(pid), round(float(rating), 3),
         int((motor.get("n") or {}).get(pid, 0)))
        for pid, rating in (motor.get("elo") or {}).items()
    ]
    if not filas:
        return 0

    hechos = 0
    for i in range(0, len(filas), lote):
        trozo = filas[i:i + lote]
        valores = ",".join(["(%s,%s,%s,%s,null,null,null,null,0,now())"] * len(trozo))
        params = tuple(v for fila in trozo for v in fila)
        ok = db.run_query(
            "insert into elo_ratings "
            "(sport, entidad_id, rating, partidos, fecha_ultimo, pf, pa, total, "
            " n_forma, actualizado) values " + valores + " "
            "on duplicate key update rating=values(rating), "
            "partidos=values(partidos), actualizado=now()",
            params,
        )
        if ok:
            hechos += len(trozo)
    return hechos


__all__ = ["cargar", "buscar_id", "predecir", "guardar_en_bd"]
