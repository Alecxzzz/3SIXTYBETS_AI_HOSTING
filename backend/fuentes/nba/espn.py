# -*- coding: utf-8 -*-
"""NBA: props de jugador resueltos con el box score de ESPN (gratis).

ESPN no necesita key y su `/summary?event=` trae el box score completo:
`boxscore.players[].statistics[].labels` = MIN, PTS, FG, 3PT, FT, REB, AST...
y `athletes[].stats` con los valores en el mismo orden.

Esto resuelve SIN IA mercados que antes se anulaban o se adivinaban:
puntos, rebotes y asistencias de un jugador.

Reglas:
  * push (linea exacta) -> ANULADO;
  * jugador que no jugo (MIN 0/vacio) -> ANULADO, nunca FALLO.
"""
from __future__ import annotations

import re
import unicodedata

import requests

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

ACIERTO = "ACIERTO"
FALLO = "FALLO"
ANULADO = "ANULADO"
_TOL = 1e-9

# Etiqueta de ESPN para cada mercado de prop.
_ETIQUETA = {
    "PTS": ("punto", " pts", "anotacion", "anota"),
    "REB": ("rebote", " reb", "tablero"),
    "AST": ("asistencia", " ast", "asist"),
}


def _norm(texto: str) -> str:
    s = (texto or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def _tokens(texto: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", _norm(texto)) if len(t) > 1}


def _parecido(a: str, b: str) -> bool:
    """Mismo jugador por apellido (palabra completa de 4+ letras)."""
    ta = {t for t in _tokens(a) if len(t) >= 4}
    tb = {t for t in _tokens(b) if len(t) >= 4}
    return bool(ta and tb and (ta & tb))


def _num(valor):
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def box_score(event_id: str) -> dict:
    """{nombre_jugador: {"pts","reb","ast","min","nombre"}} desde ESPN."""
    try:
        data = requests.get(SUMMARY, params={"event": event_id},
                            headers=UA, timeout=25).json()
    except Exception:
        return {}
    salida = {}
    for grupo in ((data.get("boxscore") or {}).get("players") or []):
        for bloque in grupo.get("statistics") or []:
            etiquetas = [str(e).upper() for e in (bloque.get("labels") or [])]
            idx = {e: i for i, e in enumerate(etiquetas)}
            for fila in bloque.get("athletes") or []:
                nombre = ((fila.get("athlete") or {}).get("displayName")
                          or (fila.get("athlete") or {}).get("shortName") or "")
                if not nombre:
                    continue
                valores = fila.get("stats") or []

                def _v(etiqueta):
                    i = idx.get(etiqueta)
                    if i is None or i >= len(valores):
                        return None
                    return _num(valores[i])

                salida[_norm(nombre)] = {
                    "nombre": nombre,
                    "pts": _v("PTS"),
                    "reb": _v("REB"),
                    "ast": _v("AST"),
                    "min": _v("MIN"),
                }
    return salida


def _mercado(texto: str):
    t = _norm(texto)
    for etiqueta, claves in _ETIQUETA.items():
        if any(k in t for k in claves):
            return etiqueta
    return None


def _linea(texto: str):
    t = _norm(texto)
    m = re.search(r"(?:over|under|mas de|menos de)\s*\+?\s*"
                  r"([0-9]+(?:[.,][0-9]+)?)", t)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\s*\+?\b", t)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _direccion(texto: str):
    t = _norm(texto)
    if "menos de" in t or re.search(r"\bunder\b", t):
        return "under"
    if "mas de" in t or re.search(r"\bover\b", t):
        return "over"
    return None


def resolver_prop(pick: dict, box: dict | None = None):
    """Resuelve un prop NBA. ACIERTO / FALLO / ANULADO / None.

    Devuelve None si el pick no es un prop de NBA o falta informacion.
    """
    market = pick.get("market") or ""
    titulo = pick.get("titulo") or ""
    sel = pick.get("selection") or ""
    texto = f"{market} {titulo} {sel}"

    # La estadistica se toma de la SELECCION primero: si el mercado dice
    # "Puntos del jugador" pero la seleccion dice "asistencias", manda la
    # seleccion (antes ganaba PTS por el orden del diccionario).
    etiqueta = _mercado(sel) or _mercado(titulo) or _mercado(market)
    if etiqueta is None:
        return None

    linea = _linea(f"{sel} {titulo}")
    direccion = _direccion(sel) or _direccion(titulo) or _direccion(market)
    if linea is None or direccion is None:
        return None

    box = box if box is not None else box_score(
        pick.get("eventId") or pick.get("event_id") or "")
    if not box:
        return None

    # El jugador se busca por la seleccion primero y luego por el titulo.
    jugador = None
    for fuente in (sel, titulo, market):
        for clave, datos in box.items():
            if _parecido(fuente, datos["nombre"]):
                jugador = datos
                break
        if jugador:
            break
    if jugador is None:
        return None

    if not jugador.get("min"):  # no jugo -> el prop se anula
        return ANULADO

    valor = jugador.get(etiqueta.lower())
    if valor is None:
        return None
    if abs(valor - linea) < _TOL:
        return ANULADO
    if direccion == "over":
        return ACIERTO if valor > linea else FALLO
    return ACIERTO if valor < linea else FALLO


__all__ = ["box_score", "resolver_prop", "ACIERTO", "FALLO", "ANULADO"]
