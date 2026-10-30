# -*- coding: utf-8 -*-
"""
Parseo de las props de JUGADOR que publica Doradobet (Altenar).

Por que existe
--------------
`cuotas_doradobet._props_jugador` solo entiende UN formato de nombre
("... por jugador (Nombre (EQUIPO))") y ademas solo mira mlb/nba/tennis.
El payload real (comprobado el 30/10/2026) trae props de jugador en todos
los deportes, pero con TRES formatos distintos:

  (A) jugador en el NOMBRE del mercado          -> MLB y futbol
      "Hits totales por jugador (Kyle Schwarber (PHI)) (incl. extra innings)"
      "Goleador - Gerard Moreno (VIL)"

  (B) jugador en la SELECCION (odd)             -> NBA y NFL
      mercado: "Puntos (incl. prorroga)"
      odd:     "Anthony Davis (WAS) Mas De 17.5"   @ 1.3334
      odd:     "Aaron Rodgers (PIT) Mas De 223.5"  @ 1.8696

  (C) mercado por jugador en 'markets'          -> tenis (fuera de v1: no
      "Alexander Zverev Aces"                      hay historico por partido)

Todo se normaliza a la MISMA fila para que service.py no sepa de que
deporte viene:

    {"jugador", "equipo", "prop", "prop_raw", "lado", "linea", "cuota",
     "mercado"}

`prop` es una clave canonica (ver STATS) que mapea al campo del historico:

    soccer: goles -> totalGoals, remates_puerta -> shotsOnTarget, ...
    nba:    puntos -> points, rebotes -> totalRebounds, ...
    nfl:    yardas_pase -> passingYards, ...
    mlb:    hits -> hits, hr -> homeRuns, ...

Nada aqui inventa datos: si una odd no tiene linea reconocible se descarta.
"""

import re
import unicodedata

# Cuota objetivo para elegir "la linea principal" de cada prop. En una
# escalera (4.5 @1.01 ... 24.5 @1.87 ... 34.5 @6.0) la linea justa es la que
# el bookmaker cotiza cerca de 2.0: esa es la que se compara con el historico.
LINEA_OBJETIVO = 1.95

# prop canonica -> campo del historico (lo consume proveedores.py)
STATS = {
    "soccer": {
        "goles": "totalGoals",
        "asistencias": "goalAssists",
        "remates": "totalShots",
        "remates_puerta": "shotsOnTarget",
        "faltas": "foulsCommitted",
        "offsides": "offsides",
    },
    "nba": {
        "puntos": "points",
        "rebotes": "totalRebounds",
        "asistencias": "assists",
        "triples": "threePointFieldGoalsMade",
        "robos": "steals",
        "tapones": "blocks",
    },
    "nfl": {
        "yardas_pase": "passingYards",
        "td_pase": "passingTouchdowns",
        "yardas_recepcion": "receivingYards",
        "recepciones": "receptions",
        "yardas_carrera": "rushingYards",
        "td_carrera": "rushingTouchdowns",
        "intercepciones": "interceptions",
        "sacks": "sacks",
    },
    "mlb": {
        "hits": "hits",
        "hr": "homeRuns",
        "ponches": "strikeOuts",
        "bases_totales": "totalBases",
        "carreras": "runs",
        "rbi": "rbi",
        "bases_robadas": "stolenBases",
    },
}

DEPORTES = tuple(STATS)

# Familia de mercado -> clave canonica. El orden IMPORTA: se evalua de
# arriba abajo y gana la primera coincidencia, asi que las variantes
# complejas van antes que las simples ("remates a puerta" antes que "remates").
# None = mercado que existe en el book pero NO es un prop comparable con el
# historico por partido (primero/ultimo goleador, multigoleadores...).
_FAMILIAS = {
    "soccer": (
        ("marcador exacto", None),
        ("por mitad", None),
        ("sustituto", None),
        ("multigoleador", None),
        ("goleador", "goles"),
        ("asistencia", "asistencias"),
        ("remates a puerta", "remates_puerta"),
        ("remate a puerta", "remates_puerta"),
        ("remates", "remates"),
        ("remate", "remates"),
        ("falta", "faltas"),
        ("fuera de juego", "offsides"),
    ),
    "nba": (
        ("puntos", "puntos"),
        ("rebotes", "rebotes"),
        ("asistencias", "asistencias"),
        ("triples", "triples"),
        ("robos", "robos"),
        ("tapones", "tapones"),
    ),
    "nfl": (
        ("yardas de pases", "yardas_pase"),
        ("touchdowns de pase", "td_pase"),
        ("yardas de recepcion", "yardas_recepcion"),
        ("recepciones", "recepciones"),
        ("yardas de carrera", "yardas_carrera"),
        ("touchdowns de carrera", "td_carrera"),
        ("intercepciones", "intercepciones"),
        ("sacks", "sacks"),
    ),
    "mlb": (
        ("carreras impulsadas", "rbi"),
        ("bases totales", "bases_totales"),
        ("bases robadas", "bases_robadas"),
        ("hits", "hits"),
        ("hr", "hr"),
        ("jonrones", "hr"),
        ("strikeouts", "ponches"),
        ("ponches", "ponches"),
        ("carreras", "carreras"),
    ),
}


# --------------------------------------------------------------- utilidades


def _norm(texto) -> str:
    """Minusculas, sin acentos y con espacios simples."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("\t", " ").split())


def _float(texto):
    try:
        return float(str(texto).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


_RE_NUM = re.compile(r"([+-]?\d+(?:[.,]\d+)?)")


def _linea_de_sv(sv):
    """La linea de la odd.

    El bookmaker la manda en 'sv', que a veces es compuesto:
        "223.5"                  -> 223.5
        "0.5|ws:player:1871|1"   -> 0.5   (Goleador: parte de apuesta)
    """
    if sv is None:
        return None
    return _float(str(sv).split("|")[0])


def _lado_de(texto):
    t = _norm(texto)
    if "menos" in t or "under" in t:
        return "under"
    if "mas" in t or "over" in t:
        return "over"
    return None


# Odds sin linea numerica que SI son un prop de "1 o mas" (si/no).
_ANYTIME = ("cualq", "cualquier momento", "anota", "marca", "si", "yes")

# Odds que son OTRO mercado (no responden a "lo hizo en el partido").
_OTRO_MERCADO = ("primero", "primer", "ultimo", "last", "first")


def _es_otro_mercado(nombre_odd: str) -> bool:
    """True para 'Primero'/'Ultimo' (primer/ultimo goleador), no son props."""
    t = _norm(nombre_odd)
    return any(t == x or t.startswith(x + " ") or t.startswith(x + "@")
               for x in _OTRO_MERCADO)


_RE_INCL = re.compile(r"\(incl\.[^)]*\)", re.I)
_RE_VARIANTE = re.compile(
    r"m[aá]s\s*/\s*menos de jugador|m[ií]\s*/\s*menos de jugador", re.I
)


def _prop_de_mercado(sport: str, nombre: str):
    """(clave canonica, texto legible) o (None, None) si no es comparable.

    Limpia los adornos del bookmaker antes de clasificar:
        "Yardas de pases Mas / Menos de Jugador (incl. prorroga)"
            -> "Yardas de pases"  -> yardas_pase
        "Hits totales por jugador"  -> "Hits totales" -> hits
    """
    limpio = _RE_INCL.sub(" ", nombre or "")
    limpio = _RE_VARIANTE.sub(" ", limpio)
    limpio = re.sub(r"\bpor jugador\b", " ", limpio, flags=re.I)
    limpio = re.sub(r"\s+", " ", limpio).strip(" -|/")
    if not limpio:
        return None, None
    t = _norm(limpio)
    for aguja, clave in _FAMILIAS.get(sport, ()):
        if aguja in t:
            return clave, limpio
    return None, None



# ------------------------------------------------- (A) jugador en el mercado

_RE_FUTBOL = re.compile(
    r"^(?P<prop>.+?)\s+-\s+(?P<jugador>.+?)\s*\((?P<equipo>[A-Z]{2,4})\)\s*$"
)
_RE_MLB = re.compile(r"^(?P<prop>.*?por jugador)\s*\((?P<resto>[^)]+)\)", re.I)
_RE_EQUIPO_FIN = re.compile(r"\((?P<eq>[A-Z]{2,4})\)?$")


def _jugador_en_mercado(sport: str, nombre: str):
    """Formato (A): (prop_raw, jugador, equipo) o None."""
    if sport == "soccer":
        m = _RE_FUTBOL.match(nombre)
        if not m:
            return None
        return m.group("prop").strip(), m.group("jugador").strip(), m.group("equipo")
    if sport == "mlb":
        m = _RE_MLB.search(nombre)
        if not m:
            return None
        resto = m.group("resto")
        eq = _RE_EQUIPO_FIN.search(resto)
        jugador = re.sub(r"\s*\([A-Z]{2,4}\)?\s*$", "", resto)
        jugador = jugador.replace("(", "").replace(")", "").strip()
        if not jugador:
            return None
        return m.group("prop").strip(), jugador, (eq.group("eq") if eq else None)
    return None


# ------------------------------------------------ (B) jugador en la seleccion

_RE_JUGADOR_EN_ODD = re.compile(
    r"^(?P<jugador>.+?)\s*\((?P<equipo>[A-Z]{2,4})\)\s*"
    r"(?P<lado>m[aá]s|menos|over|under)\s*(?:de\s*)?"
    r"(?P<linea>[+-]?\d+(?:[.,]\d+)?)\s*$",
    re.I,
)


def _odds_de(cm: dict, odds_idx: dict) -> list:
    """Todas las odds reales de un childMarket (deduplicadas por id)."""
    salida, vistos, grupos = [], set(), []
    for clave in ("desktopOddIds", "oddIds", "mobileOddIds"):
        valor = cm.get(clave)
        if valor:
            grupos.append(valor)
    for grupo in grupos:
        if not isinstance(grupo, (list, tuple)):
            grupo = [grupo]
        for item in grupo:
            ids = item if isinstance(item, (list, tuple)) else [item]
            for oid in ids:
                if oid in vistos:
                    continue
                vistos.add(oid)
                odd = odds_idx.get(oid)
                if odd:
                    salida.append(odd)
    return salida



def _fila(sport, prop, prop_raw, jugador, equipo, odd, mercado):
    """Normaliza una odd a la fila comun. None si no es prop comparable."""
    nombre_odd = str(odd.get("name") or "").strip()
    if not nombre_odd or _es_otro_mercado(nombre_odd):
        return None
    cuota = _float(odd.get("price"))
    if not cuota or cuota <= 1.0:
        return None

    lado = _lado_de(nombre_odd)
    linea = _linea_de_sv(odd.get("sv"))
    if linea is None:
        m = _RE_NUM.search(nombre_odd)
        linea = _float(m.group(1)) if m else None

    if jugador is None:
        m = _RE_JUGADOR_EN_ODD.match(nombre_odd)
        if not m:
            return None
        jugador = m.group("jugador").strip()
        equipo = m.group("equipo")
        lado = _lado_de(m.group("lado")) or lado
        linea = _float(m.group("linea"))

    if linea is None:
        # "Cualq. Momen" / "Si" / "Anota" = prop de "1 o mas".
        if any(x in _norm(nombre_odd) for x in _ANYTIME):
            linea = 0.5
        else:
            return None

    return {
        "jugador": jugador.strip(),
        "equipo": (equipo or "").upper()[:4] or None,
        "prop": prop,
        "prop_raw": (prop_raw or "").strip(),
        "lado": lado or "over",
        "linea": round(float(linea), 2),
        "cuota": round(float(cuota), 4),
        "mercado": mercado,
    }


def props_de_detalle(sport: str, detalle: dict) -> list:
    """Props de jugador normalizadas de un GetEventDetails.

    Recorre los childMarkets (donde viven las props en Altenar) y aplica el
    parser del deporte. Devuelve filas deduplicadas por
    (jugador, prop, lado, linea).
    """
    if not detalle or sport not in STATS:
        return []
    odds_idx = {o.get("id"): o for o in (detalle.get("odds") or [])}
    salida, vistos = [], set()

    def _anadir(fila):
        if not fila:
            return
        clave = (fila["jugador"].lower(), fila["prop"], fila["lado"], fila["linea"])
        if clave in vistos:
            return
        vistos.add(clave)
        salida.append(fila)

    for cm in detalle.get("childMarkets") or []:
        nombre = str(cm.get("name") or "").strip()
        if not nombre:
            continue
        datos = _jugador_en_mercado(sport, nombre)
        if datos:
            prop_raw, jugador, equipo = datos
            prop, _legible = _prop_de_mercado(sport, prop_raw)
            if not prop:
                continue
            for odd in _odds_de(cm, odds_idx):
                _anadir(_fila(sport, prop, prop_raw, jugador, equipo, odd, nombre))
            continue
        # (B) el jugador viene en la seleccion: NBA y NFL
        if sport in ("nba", "nfl"):
            prop, legible = _prop_de_mercado(sport, nombre)
            if not prop:
                continue
            for odd in _odds_de(cm, odds_idx):
                _anadir(_fila(sport, prop, legible, None, None, odd, nombre))
    return salida


def elegir_principal(props: list) -> list:
    """Una fila por (jugador, prop): la linea cuya cuota esta mas cerca del
    objetivo (~2.0). De una escalera de 10 lineas, esa es la linea justa.

    Desempate a favor de "over": el bookmaker publica el mismo prop por los dos
    lados y un apartado de props se lee como "llegara a X" (p. ej. "15+ puntos"),
    no "se quedara por debajo de X". El 'under' solo se elige si esta
    claramente mas cerca de 2.0.
    """
    _PENALIZACION_UNDER = 0.05
    mejores = {}
    for p in props:
        clave = (p["jugador"].lower(), p["prop"])
        distancia = abs(float(p["cuota"]) - LINEA_OBJETIVO)
        if (p.get("lado") or "over") == "under":
            distancia += _PENALIZACION_UNDER
        actual = mejores.get(clave)
        if actual is None or distancia < actual[0]:
            mejores[clave] = (distancia, p)
    return [p for _d, p in mejores.values()]

