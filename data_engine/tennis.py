# -*- coding: utf-8 -*-
"""TENIS: detalle y resolucion determinista con ESPN (sin IA, sin pagar).

Por que un modulo aparte
------------------------
`data_engine.espn._parse_evento_scoreboard` arreglaba tres bugs, pero **el
resolutor de produccion no lo usaba**: iba por `sports.get_game_detail`, que
en tenis:

  * fuerza `homeAway` por el ORDEN del array (ESPN lo manda al reves del
    cuadro) -> veredicto INVERTIDO ("Rune gano y se marcaba FALLO");
  * deja `score = null` (ESPN no pone marcador en tenis) -> el pick llegaba
    sin marcador y no se podia decidir nada.

Aqui se lee el partido directo del scoreboard y se normaliza:

  * `winner` del competidor  -> quien gano (fiable incluso en retirada)
  * `linescores`             -> JUEGOS de cada set (6-4, 7-6...)
  * `status.type.name`       -> STATUS_FINAL / STATUS_RETIRED / STATUS_WALKOVER
  * `notes[0].text`          -> "A bt B 6-3 7-6 (9-7)" (2a verificacion)

Reglas de mercado (las que aplican los books):
  * FINAL    -> ganador, sets y juegos se resuelven; push -> ANULADO.
  * RETIRED  -> el GANADOR se paga; totales/handicaps -> ANULADO.
  * WALKOVER -> igual que RETIRED.
  * 7-6 cuenta como 13 juegos (6+6+1), como en la cuota.
"""
from __future__ import annotations

import re
import unicodedata

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/tennis"
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


def _norm(texto: str) -> str:
    s = (texto or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def _tokens(texto: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", _norm(texto)) if len(t) > 1}


def _parecido(a: str, b: str) -> bool:
    """Mismo jugador por apellido: 'H. Rune' ~ 'Holger Rune'.

    Exige una palabra de 4+ letras en comun para no confundir 'Rune' con
    'Ruud' (comparten 'ru' pero no la palabra completa).
    """
    ta = {t for t in _tokens(a) if len(t) >= 4}
    tb = {t for t in _tokens(b) if len(t) >= 4}
    if not ta or not tb:
        return False
    return bool(ta & tb)


def _get(url: str, timeout: int = 20) -> dict:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _juegos_de(linescores) -> list:
    """Juegos de cada set. Soporta dicts de ESPN y valores sueltos."""
    out = []
    for s in linescores or []:
        v = s.get("displayValue", s.get("value")) if isinstance(s, dict) else s
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _nombre(competidor: dict) -> str:
    ath = competidor.get("athlete") or {}
    roster = competidor.get("roster") or {}
    return (
        roster.get("displayName")
        or ath.get("displayName")
        or ath.get("fullName")
        or "?"
    )


def _competencia(data: dict, event_id: str):
    """Busca la competencia (partido) por id dentro de los groupings."""
    for ev in data.get("events") or []:
        for g in ev.get("groupings") or []:
            for comp in g.get("competitions") or []:
                if str(comp.get("id")) == str(event_id):
                    return ev, g, comp
    return None, None, None


def detalle(pick: dict, tour: str | None = None) -> dict | None:
    """Detalle del partido de tenis, ya alineado a los nombres del pick.

    Devuelve {"estado", "estado_detallado", "nota", "teams": [...]} con
    `sets` y `juegos` calculados, o None si ESPN no lo encuentra.
    """
    event_id = str(pick.get("eventId") or pick.get("event_id") or "")
    if not event_id:
        return None
    home = pick.get("homeName") or pick.get("home_name") or ""
    away = pick.get("awayName") or pick.get("away_name") or ""

    for t in ([tour] if tour else ["atp", "wta"]):
        try:
            data = _get(f"{ESPN}/{t}/scoreboard?event={event_id}")
        except Exception:
            continue
        ev, grupo, comp = _competencia(data, event_id)
        if not comp:
            continue

        tipo = ((comp.get("status") or {}).get("type") or {})
        nombre_estado = (tipo.get("name") or "").upper()
        estado = "post" if tipo.get("completed") else \
            ("in" if tipo.get("state") == "in" else "pre")

        equipos = []
        for c in comp.get("competitors") or []:
            juegos = _juegos_de(c.get("linescores"))
            sets_ganados = sum(
                1 for s in (c.get("linescores") or [])
                if isinstance(s, dict) and s.get("winner")
            )
            if not sets_ganados and c.get("winner") is True:
                sets_ganados = 1
            equipos.append({
                "nombre": _nombre(c),
                "lado": (c.get("homeAway") or "").lower() or None,
                "score": float(sets_ganados),
                "juegos": juegos,
                "juegos_total": sum(juegos),
                "linescores": c.get("linescores") or [],
                "winner": c.get("winner"),
            })

        if len(equipos) < 2:
            continue

        # Alinear con los nombres del PICK (nunca con el orden del array).
        i_home = next((i for i, e in enumerate(equipos)
                       if _parecido(e["nombre"], home)), None)
        i_away = next((i for i, e in enumerate(equipos)
                       if _parecido(e["nombre"], away)), None)
        if i_home is not None and i_away is not None and i_home != i_away:
            equipos[i_home]["lado"] = "home"
            equipos[i_away]["lado"] = "away"
        else:
            equipos[0]["lado"] = "home"
            equipos[1]["lado"] = "away"

        nota = ""
        for n in comp.get("notes") or []:
            if isinstance(n, dict) and n.get("text"):
                nota = n["text"]
                break

        return {
            "estado": estado,
            "estado_detallado": nombre_estado or "STATUS_FINAL",
            "nota": nota,
            "teams": equipos,
            "torneo": ev.get("name") if ev else None,
            "rama": (grupo.get("grouping") or {}).get("slug") if grupo else None,
        }
    return None


# ---------------------------------------------------------------------------
# Resolucion
# ---------------------------------------------------------------------------
def _linea(texto: str):
    t = _norm(texto)
    m = re.search(
        r"(?:over|under|mas de|menos de|total|juegos|handicap)\s*\+?\s*"
        r"([0-9]+(?:[.,][0-9]+)?)", t)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"\(\s*([+-]?[0-9]+(?:[.,][0-9]+)?)\s*\)", t)
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


def _lado(texto: str, home: dict, away: dict):
    """Lado al que apunta la seleccion, por apellido (4+ letras)."""
    if _parecido(texto, home["nombre"]) and not _parecido(texto, away["nombre"]):
        return "home"
    if _parecido(texto, away["nombre"]) and not _parecido(texto, home["nombre"]):
        return "away"
    return None


def _por_direccion(valor: float, linea_: float, dir_: str) -> str:
    if abs(valor - linea_) < _TOL:
        return ANULADO  # push
    if dir_ == "over":
        return ACIERTO if valor > linea_ else FALLO
    return ACIERTO if valor < linea_ else FALLO


def resolver(pick: dict, det: dict | None):
    """Resuelve un pick de tenis. ACIERTO / FALLO / ANULADO / None.

    ANULADO cubre los tres casos que antes se marcaban como FALLO:
      * push (linea exacta),
      * retirada (STATUS_RETIRED),
      * walkover (STATUS_WALKOVER).
    En retirada/walkover el GANADOR si se resuelve (los books lo pagan).
    """
    if not det or det.get("estado") != "post" or len(det.get("teams") or []) < 2:
        return None

    home = next((t for t in det["teams"] if t.get("lado") == "home"), None)
    away = next((t for t in det["teams"] if t.get("lado") == "away"), None)
    if home is None or away is None:
        return None

    estado = str(det.get("estado_detallado") or "").upper()
    sin_juego = estado in ("STATUS_RETIRED", "STATUS_WALKOVER")

    market = _norm(pick.get("market") or "")
    titulo = _norm(pick.get("titulo") or "")
    sel = pick.get("selection") or ""
    texto = f"{market} {titulo} {_norm(sel)}".strip()
    mios = _lado(f"{sel} {titulo}", home, away)

    # 1) Ganador (se paga tambien en retirada/walkover).
    if "ganador" in texto or "winner" in texto or re.search(r"\bgana\b", texto):
        if home.get("winner") is True:
            ganador = "home"
        elif away.get("winner") is True:
            ganador = "away"
        elif home["score"] != away["score"]:
            ganador = "home" if home["score"] > away["score"] else "away"
        else:
            return None
        if mios is None:
            return None
        return ACIERTO if mios == ganador else FALLO

    # 2) Total de juegos.
    # La direccion sale de la SELECCION primero: el mercado puede llamarse
    # "Total juegos (under/over)" y esa palabra pisaba la seleccion real.
    if "juego" in texto and (_direccion(sel) or _direccion(titulo) or "total" in texto):
        if sin_juego:
            return ANULADO
        linea_ = _linea(texto)
        dir_ = _direccion(sel) or _direccion(titulo)
        if linea_ is None or dir_ is None:
            return None
        total = home["juegos_total"] + away["juegos_total"]
        return _por_direccion(total, linea_, dir_)

    # 3) Handicap de juegos.
    if "juego" in texto and ("handicap" in texto or "hándicap" in texto):
        if sin_juego:
            return ANULADO
        linea_ = _linea(f"{sel} {titulo}")
        if linea_ is None or mios is None:
            return None
        margen = home["juegos_total"] - away["juegos_total"]
        if mios == "away":
            margen = -margen
        return _por_direccion(margen + linea_, 0.0, "over")

    # 4) Handicap de sets.
    if "set" in texto and ("handicap" in texto or "hándicap" in texto):
        if sin_juego:
            return ANULADO
        linea_ = _linea(f"{sel} {titulo}")
        if linea_ is None or mios is None:
            return None
        margen = home["score"] - away["score"]
        if mios == "away":
            margen = -margen
        return _por_direccion(margen + linea_, 0.0, "over")

    # 5) Ganador del primer set.
    if "primer set" in texto or "1er set" in texto or "primera manga" in texto:
        if sin_juego:
            return ANULADO
        if not home["juegos"] or not away["juegos"]:
            return None
        if mios is None:
            return None
        gano_home = home["juegos"][0] > away["juegos"][0]
        return ACIERTO if (mios == "home") == gano_home else FALLO

    # 6) Sets exactos (2-0 / 2-1 / 0-2 / 1-2).
    m = re.search(r"\b([0-3])\s*-\s*([0-3])\b", _norm(sel))
    if m and "set" in texto:
        if sin_juego:
            return ANULADO
        marcador = f"{int(home['score'])}-{int(away['score'])}"
        return ACIERTO if marcador == f"{m.group(1)}-{m.group(2)}" else FALLO

    return None


__all__ = ["detalle", "resolver", "ACIERTO", "FALLO", "ANULADO"]


