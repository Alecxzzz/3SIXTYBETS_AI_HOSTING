# -*- coding: utf-8 -*-
"""SACKMANN: descarga y cache local de los CSV historicos de tenis.

Los repositorios originales `JeffSackmann/tennis_atp` y `tennis_wta` **ya no
existen** (404). El archivo vive en un espejo de un tercero, asi que aqui se
descarga UNA vez y se guarda en `backend/fuentes/tennis/cache/`. El motor
nunca lee del espejo en runtime: si el espejo cae, el motor sigue.

Licencia del dato: **CC BY-NC-SA 4.0** (atribucion obligatoria, uso no
comercial). Se cita la fuente en el docstring y en el README del modulo.
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
from datetime import datetime

import requests

ESPEJO = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
UA = {"User-Agent": "Mozilla/5.0 (compatible; 3SixtyBets/1.0)"}

# Columnas que usa el motor. El CSV trae 49; no se cargan las que no se usan
# para no inflar memoria con 20 anos de partidos.
CAMPOS = (
    "tourney_date", "tourney_name", "surface", "tourney_level", "round",
    "best_of", "score", "minutes",
    "winner_id", "winner_name", "winner_rank", "winner_rank_points",
    "loser_id", "loser_name", "loser_rank", "loser_rank_points",
    "w_ace", "w_df", "w_svpt", "w_1stWon", "w_2ndWon",
    "l_ace", "l_df", "l_svpt", "l_1stWon", "l_2ndWon",
)


def _ruta(tour: str, anio: int) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"{tour}_matches_{anio}.csv")


def _descargar(tour: str, anio: int) -> str | None:
    """Baja un CSV (viene en gzip) y lo guarda descomprimido. Devuelve texto."""
    url = f"{ESPEJO}/{tour}/{tour}_matches_{anio}.csv"
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200:
            return None
        crudo = r.content
        if crudo[:2] == b"\x1f\x8b":
            crudo = gzip.decompress(crudo)
        texto = crudo.decode("utf-8", "ignore")
    except Exception:
        return None
    try:
        with open(_ruta(tour, anio), "w", encoding="utf-8", newline="") as fh:
            fh.write(texto)
    except OSError:
        pass
    return texto


def _leer(tour: str, anio: int) -> str | None:
    """Texto del CSV: primero la cache local, si no descarga."""
    ruta = _ruta(tour, anio)
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8", errors="ignore") as fh:
                return fh.read()
        except OSError:
            pass
    return _descargar(tour, anio)


def _num(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def partidos(tour: str = "atp", desde: int = 2018, hasta: int | None = None) -> list:
    """Partidos normalizados de `desde`..`hasta` (por defecto, hasta hoy).

    Devuelve dicts con lo minimo para calibrar y predecir. Se descartan los
    partidos sin ids o sin fecha (no se pueden ordenar).
    """
    hasta = hasta or datetime.now().year
    salida = []
    for anio in range(desde, hasta + 1):
        texto = _leer(tour.lower(), anio)
        if not texto:
            continue
        for fila in csv.DictReader(io.StringIO(texto)):
            fecha = (fila.get("tourney_date") or "").strip()
            if len(fecha) < 8 or not fila.get("winner_id") or not fila.get("loser_id"):
                continue
            item = {k: fila.get(k) for k in CAMPOS}
            item["anio"] = anio
            item["premios"] = None
            item["puntos_ganador"] = _num(fila.get("winner_rank_points"))
            item["puntos_perdedor"] = _num(fila.get("loser_rank_points"))
            item["rank_ganador"] = _num(fila.get("winner_rank"))
            item["rank_perdedor"] = _num(fila.get("loser_rank"))
            item["best_of"] = int(_num(fila.get("best_of")) or 3)
            salida.append(item)
    salida.sort(key=lambda f: (f.get("tourney_date") or "", f.get("round") or ""))
    return salida


def juegos_del_score(score: str):
    """Juegos totales de un marcador ('6-4 7-6(5)' -> 23).

    Devuelve None en retiradas/walkovers: ese marcador esta incompleto y
    usarlo como total seria mentira (ESPN lo marca aparte).
    """
    if not score:
        return None
    alto = score.upper()
    if any(k in alto for k in ("RET", "W/O", "DEF", "ABN")):
        return None
    total = 0
    sets = 0
    for trozo in score.replace(";", " ").split():
        # El tiebreak va entre parentesis ('7-6(5)') y NO son juegos.
        trozo = re.sub(r"\([^)]*\)", "", trozo)
        partes = trozo.split("-")
        if len(partes) != 2:
            continue
        try:
            total += int(partes[0]) + int(partes[1])
            sets += 1
        except ValueError:
            continue
    return total if sets >= 2 else None


__all__ = ["partidos", "juegos_del_score", "CACHE", "ESPEJO"]
