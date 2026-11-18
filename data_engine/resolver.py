# -*- coding: utf-8 -*-
"""Resolutor DETERMINISTA de picks (v2).

Sustituye a `dashboard._resolver_deterministico`, que tenia cuatro bugs
medidos que falseaban resultados reales:

1. **Unders invertidos**: `es_over = tipo.startswith(("over", "m"))` y
   `"menos de"` empieza por "m" -> TODOS los under se evaluaban como over.
   Un Under 2.5 con 1-0 (1 gol) se marcaba FALLO; con 3 goles, ACIERTO.
   Es el bug que hacia desaparecer picks verdaderos como perdidos y
   aparecer perdidos como verdaderos.

2. **Sin rama de handicap/spread**: el catalogo publica "Handicap europeo o
   asiatico" pero el resolutor no tenia ninguna regla, asi que esos picks
   no se resolvian nunca (acababan borrados a las 72 h).

3. **Sin push**: `total > linea` / `total < linea` devolvia FALLO cuando el
   total caia EXACTAMENTE en la linea. Un push es una devolucion (ANULADO),
   nunca un fallo.

4. **Mitades por indice**: `_resolver_por_mitades` usaba `teams[0]` como
   local y `teams[1]` como visitante. ESPN no garantiza ese orden; si venia
   invertido, el veredicto se daba la vuelta.

API (una sola funcion pura):

    resolver_mercado(pick, detalle) -> "ACIERTO" | "FALLO" | "ANULADO" | None

`None` = "no decidible con este detalle" -> el llamador lo deja PENDIENTE,
nunca lo borra.
"""
from __future__ import annotations

import re
import unicodedata

ACIERTO = "ACIERTO"
FALLO = "FALLO"
ANULADO = "ANULADO"

# Mercados que dependen de estadisticas, no del marcador: aqui NUNCA se
# comparan contra el total de goles (bug real: 'Tarjetas Over 2.5' salia
# ACIERTO porque el partido tuvo 3 goles).
_ES_STATS = (
    "tarjeta", "card", "corner", "esquina", "falta", "foul",
    "offside", "fuera de juego", "remate", "tiro", "shot", "saque",
    " ace", "dobles faltas", "double fault",
)

_TOL = 1e-9


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------
def norm(texto: str) -> str:
    """Minusculas, sin acentos y con espacios simples."""
    s = (texto or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def _tokens(texto: str) -> set:
    """Palabras significativas (2+ caracteres) de un texto normalizado."""
    return {t for t in re.split(r"[^a-z0-9]+", norm(texto)) if len(t) > 1}


_STOP = {"fc", "cf", "cd", "sc", "ac", "afc", "nfc", "club", "de", "del", "the"}


def _clave_equipo(nombre: str) -> set:
    return {t for t in _tokens(nombre) if t not in _STOP}


def equipo_en_texto(texto: str, nombre: str) -> bool:
    """True si TODAS las palabras clave del equipo aparecen en el texto.

    Se compara por palabras completas (no por substring) para no confundir
    'Real Madrid' con 'Real Madrid Castilla' ni 'Atletico Nacional' con
    'Atletico Mineiro'.
    """
    claves = _clave_equipo(nombre)
    if not claves or not any(len(t) >= 3 for t in claves):
        return False
    return claves.issubset(_tokens(texto))


def _lado_en_texto(texto: str, home: dict, away: dict):
    """'home' / 'away' segun el texto, o None si no lo identifica.

    Si menciona a los DOS equipos se queda con el nombre MAS ESPECIFICO:
    'Real Madrid Castilla' le gana a 'Real Madrid' (3 palabras contra 2).
    Sin esa regla, un pick del filial se resolvia contra el primer equipo.
    """
    ch = _clave_equipo(home.get("nombre") or "")
    ca = _clave_equipo(away.get("nombre") or "")
    th = _tokens(texto)
    mh = bool(ch) and ch.issubset(th)
    ma = bool(ca) and ca.issubset(th)
    if mh and ma:
        if len(ca) > len(ch):
            return "away"
        if len(ch) > len(ca):
            return "home"
        return None
    if mh:
        return "home"
    if ma:
        return "away"
    return None


def _dice_no(texto: str) -> bool:
    """True si la respuesta es NO.

    Se mira la PRIMERA palabra: `startswith("no")` marcaba 'Nacional' como
    NO (bug real en los mercados SI/NO).
    """
    partes = norm(texto).split()
    return bool(partes) and partes[0] in ("no", "nope", "never")


# ---------------------------------------------------------------------------
# Detalle del partido
# ---------------------------------------------------------------------------
def _equipos(detalle: dict):
    """(home, away) normalizados. Respeta `homeAway`; NUNCA usa el indice."""
    raw = (detalle or {}).get("teams") or (detalle or {}).get("equipos") or []
    equipos = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        score = t.get("score")
        if isinstance(score, dict):
            score = score.get("value", score.get("displayValue"))
        equipos.append({
            "nombre": t.get("name") or t.get("nombre") or "",
            "score": score,
            "lado": (t.get("homeAway") or t.get("home_away") or "").lower() or None,
            "linescores": t.get("linescores") or [],
        })
    if len(equipos) < 2:
        return None, None
    home = next((e for e in equipos if e["lado"] == "home"), None)
    away = next((e for e in equipos if e["lado"] == "away"), None)
    if home is None or away is None:
        home, away = equipos[0], equipos[1]
    return home, away


def _puntos(equipo: dict):
    try:
        return float(equipo.get("score"))
    except (TypeError, ValueError):
        return None


def _periodos(equipo: dict):
    """Marcador por periodo (linescores) normalizado a floats."""
    out = []
    for p in equipo.get("linescores") or []:
        v = p.get("displayValue", p.get("value")) if isinstance(p, dict) else p
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


# ---------------------------------------------------------------------------
# Resolucion
# ---------------------------------------------------------------------------
def _por_direccion(valor: float, linea_: float, dir_: str) -> str:
    """ACIERTO/FALLO/ANULADO para un over/under. Push => ANULADO."""
    if abs(valor - linea_) < _TOL:
        return ANULADO
    if dir_ == "over":
        return ACIERTO if valor > linea_ else FALLO
    return ACIERTO if valor < linea_ else FALLO


def _resolver_mitades(texto: str, sel: str, home: dict, away: dict):
    """Mercados de mitad con los linescores, alineados por homeAway."""
    es_ambas = "ambas mitades" in texto
    es_cualquier = (not es_ambas) and (
        "cualquier mitad" in texto or "alguna mitad" in texto
    )
    es_primera = bool(re.search(r"\b1\s*(?:a|er|ra)?\s*(?:mitad|tiempo|half)\b", texto)) \
        or "primera mitad" in texto or "primer tiempo" in texto
    # "2ª Mitad - 1x2" es un mercado que el book publica y que si se puede
    # resolver: ESPN manda un linescore por periodo. Antes no se contemplaba y
    # el pick quedaba PENDIENTE para siempre.
    es_segunda = bool(re.search(r"\b2\s*(?:a|da|ª)?\s*(?:mitad|tiempo|half)\b", texto)) \
        or "segunda mitad" in texto or "segundo tiempo" in texto
    if not (es_ambas or es_cualquier or es_primera or es_segunda):
        return None

    ph, pa = _periodos(home), _periodos(away)
    if not ph or not pa:
        return None
    n = min(len(ph), len(pa))
    if n == 0:
        return None

    lado = _lado_en_texto(f"{texto} {sel}", home, away)

    def _periodo(i: int):
        """Veredicto de UN periodo, con el lado del pick (no asume local)."""
        if lado is None or i >= n:
            return None
        propio = ph[i] if lado == "home" else pa[i]
        rival = pa[i] if lado == "home" else ph[i]
        if propio == rival:
            return ANULADO
        return ACIERTO if propio > rival else FALLO

    if es_primera and not (es_ambas or es_cualquier):
        return _periodo(0)
    if es_segunda and not (es_ambas or es_cualquier):
        return _periodo(1)

    if lado is None:
        return None
    propias = ph[:n] if lado == "home" else pa[:n]
    rivales = pa[:n] if lado == "home" else ph[:n]

    if es_ambas:
        return ACIERTO if all(p > r for p, r in zip(propias, rivales)) else FALLO

    gano_alguna = any(p > r for p, r in zip(propias, rivales))
    quiere = not _dice_no(sel)
    return ACIERTO if gano_alguna == quiere else FALLO


def resolver_mercado(pick: dict, detalle: dict):
    """Resuelve un pick contra el detalle final del partido.

    Devuelve "ACIERTO", "FALLO", "ANULADO" o None (no decidible / no es su
    mercado). El llamador debe tratar None como "dejar PENDIENTE".
    """
    home, away = _equipos(detalle or {})
    if not home or not away:
        return None
    hs, as_ = _puntos(home), _puntos(away)

    market = norm(pick.get("market") or "")
    titulo = norm(pick.get("titulo") or "")
    sel = pick.get("selection") or ""
    texto = f"{market} {titulo} {norm(sel)}".strip()
    if not texto:
        return None

    # 0) TENIS: sets, juegos, primer set y retiradas. Va ANTES de todo porque
    #    el "marcador" de un partido de tenis son los SETS, no goles.
    if str(pick.get("sport") or "").lower() in ("tennis", "tenis") or (detalle or {}).get("es_tenis"):
        return _resolver_tenis(pick, detalle)

    # 1) Mercados de estadistica: NO los decide el marcador.
    if any(k in texto for k in _ES_STATS):
        return None

    # 2) Mitades (linescores + homeAway).
    r = _resolver_mitades(texto, sel, home, away)
    if r is not None:
        return r

    # 3) Ganador / moneyline. "1x2" tambien es ganador: el mercado se llama asi
    #    en la lista del book ("Islandia (1x2)") y antes no se reconocia, asi que
    #    el pick quedaba PENDIENTE aunque el partido estuviera terminado.
    es_ganador = any(k in texto for k in ("ganador", "moneyline", "winner", " ml", "1x2")) \
        or bool(re.search(r"\bgana\b", texto))
    if es_ganador and "prorroga" not in texto and "mitad" not in texto:
        if hs is None or as_ is None:
            return None
        if hs == as_:
            return ACIERTO if "empate" in norm(sel) else FALLO
        ganador = "home" if hs > as_ else "away"
        elegido = _lado_en_texto(f"{sel} {titulo}", home, away)
        if elegido is None:
            return None
        return ACIERTO if elegido == ganador else FALLO

    # 4) Ambos marcan / BTTS.
    if ("ambos" in texto and ("marcan" in texto or "anotan" in texto)) or "btts" in texto:
        if hs is None or as_ is None:
            return None
        ambos = hs > 0 and as_ > 0
        return ACIERTO if ambos == (not _dice_no(sel)) else FALLO

    # 5) Doble oportunidad.
    if "doble oportunidad" in texto or "doble oport" in texto:
        codigo = norm(sel).replace(" ", "")
        if codigo in ("1x", "x2", "12"):
            if hs == as_:
                return ACIERTO if codigo in ("1x", "x2") else FALLO
            if codigo == "1x":
                return ACIERTO if hs > as_ else FALLO
            if codigo == "x2":
                return ACIERTO if as_ > hs else FALLO
            return ACIERTO  # 12 = gano uno de los dos
        menciona_empate = "empate" in norm(sel)
        lado = _lado_en_texto(f"{sel} {titulo}", home, away)
        if lado is None and not menciona_empate:
            return None
        if hs == as_:
            return ACIERTO if menciona_empate else FALLO
        cubre = (lado == "home") if hs > as_ else (lado == "away")
        return ACIERTO if cubre else FALLO

    # 6) Handicap / spread (con push -> ANULADO).
    if "handicap" in texto or "spread" in texto:
        linea_ = linea(f"{sel} {titulo} {market}")
        lado = _lado_en_texto(f"{sel} {titulo}", home, away)
        if linea_ is None or lado is None or hs is None or as_ is None:
            return None
        margen = (hs - as_) if lado == "home" else (as_ - hs)
        res = margen + linea_
        if abs(res) < _TOL:
            return ANULADO
        return ACIERTO if res > 0 else FALLO

    # 7) Over/Under (total del partido o de un equipo).
    # La direccion se toma de la SELECCION primero: hay mercados que se
    # llaman "Total juegos (under/over)" y la palabra 'under' del NOMBRE
    # pisaba la seleccion real ("Over 22.5" -> se resolvia como under).
    dir_ = direccion(sel) or direccion(titulo) or direccion(market)
    linea_ = linea(f"{sel} {titulo} {market}")
    if dir_ and linea_ is not None and hs is not None and as_ is not None:
        es_por_equipo = any(
            k in texto for k in ("equipo", "total de goles", "totales", "team total")
        )
        if es_por_equipo:
            lado = _lado_en_texto(f"{sel} {titulo}", home, away)
            if lado is None:
                return None
            valor = hs if lado == "home" else as_
        else:
            valor = hs + as_
        return _por_direccion(valor, linea_, dir_)

    return None


def resolver_total(valor: float, linea_: float, dir_: str) -> str:
    """ACIERTO/FALLO/ANULADO para un total ya calculado (tarjetas, corners...).

    Se expone publico porque el resolutor de ESTADISTICAS (FotMob) calcula el
    total por su cuenta y necesitaba la misma regla de push y de direccion.
    Antes comparaba `total > linea` / `< linea`, asi que una linea exacta
    devolvia FALLO en ambos sentidos.
    """
    return _por_direccion(valor, linea_, dir_)


def lado_en_texto(texto: str, home: str, away: str):
    """'home' / 'away' a partir de los NOMBRES (para el resolutor de stats)."""
    return _lado_en_texto(texto, {"nombre": home or ""}, {"nombre": away or ""})


def metricas_stats(texto: str):
    """Metricas de FotMob que corresponden a un mercado de estadistica.

    Devuelve una tupla (yellow_cards/red_cards/corners) o None si el mercado
    no es de estadistica. 'Amarillas' NO suma las rojas y viceversa: antes se
    sumaban las dos y un pick de amarillas salia ACIERTO por una roja.
    """
    t = norm(texto)
    if "tarjeta" in t or "card" in t:
        if "amarilla" in t or "yellow" in t:
            return ("yellow_cards",)
        if "roja" in t or "red card" in t or " red" in t:
            return ("red_cards",)
        return ("yellow_cards", "red_cards")
    if "corner" in t or "esquina" in t:
        return ("corners",)
    return None


def _resolver_tenis(pick: dict, detalle: dict):
    """Mercados de tenis con sets/juegos. ANULADO en push y en retirada.

    La bandera `winner` de ESPN es la fuente mas fiable para el ganador; los
    juegos salen de `linescores` (juegos de cada set). En retirada o walkover
    el marcador esta incompleto: totales y handicaps se ANULAN, nunca FALLO.
    """
    home, away = _equipos(detalle or {})
    if not home or not away:
        return None

    raw = (detalle or {}).get("teams") or (detalle or {}).get("equipos") or []

    def _bandera(eq):
        for t in raw:
            if isinstance(t, dict) and norm(t.get("name") or "") == norm(eq.get("nombre") or ""):
                return t.get("winner")
        return None

    texto = norm(f"{pick.get('market')} {pick.get('titulo')} {pick.get('selection')}")
    sel = pick.get("selection") or ""
    lado = _lado_en_texto(sel or pick.get("titulo") or "", home, away)

    sets_h, sets_a = _puntos(home), _puntos(away)
    ph, pa = _periodos(home), _periodos(away)
    juegos_h, juegos_a = sum(ph), sum(pa)
    tiene_periodos = bool(ph and pa)

    # ESPN deja `score` vacio en tenis: los sets se cuentan de los linescores.
    if sets_h is None and tiene_periodos:
        sets_h = float(sum(1 for a, b in zip(ph, pa) if a > b))
        sets_a = float(sum(1 for a, b in zip(ph, pa) if b > a))

    estado = norm(" ".join(str((detalle or {}).get(k) or "") for k in
                           ("estado_detallado", "detail", "notas", "nota")))
    incompleto = any(k in estado for k in ("ret", "walkover", "w/o", "abn"))

    # Ganador: bandera explicita o, si no viene, sets ganados.
    if re.search(r"\b(ganador|winner|moneyline)\b", texto) or "gana el partido" in texto:
        if lado is None:
            return None
        flag = _bandera(home if lado == "home" else away)
        if flag is True:
            return ACIERTO
        if flag is False:
            return FALLO
        if sets_h is None or sets_a is None:
            return None
        propio, rival = (sets_h, sets_a) if lado == "home" else (sets_a, sets_h)
        return ACIERTO if propio > rival else FALLO

    if not tiene_periodos:
        return None

    # Resultado exacto de sets (2-0, 2-1, 0-2, 1-2).
    m = re.search(r"\b([0-3])\s*-\s*([0-3])\b", sel)
    if m and re.search(r"\b(set|resultado|marcador)\b", texto):
        if incompleto:
            return ANULADO
        if sets_h is None or sets_a is None:
            return None
        return ACIERTO if (int(m.group(1)) == sets_h and int(m.group(2)) == sets_a) else FALLO

    # Handicap de juegos / de sets (linea firmada en la seleccion).
    if "handicap" in texto:
        mf = re.search(r"([+-]\s*\d+(?:[.,]\d+)?)", sel)
        if not mf or lado is None:
            return None
        try:
            lin = float(mf.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            return None
        if "juego" in texto or "game" in texto:
            if incompleto:
                return ANULADO
            margen = juegos_h - juegos_a
        elif "set" in texto:
            if incompleto:
                return ANULADO
            if sets_h is None or sets_a is None:
                return None
            margen = sets_h - sets_a
        else:
            return None
        if lado == "away":
            margen = -margen
        res = margen + lin
        if abs(res) < _TOL:
            return ANULADO
        return ACIERTO if res > 0 else FALLO

    # Total de juegos.
    if re.search(r"\b(total|juegos|games)\b", texto):
        if incompleto:
            return ANULADO
        dir_ = direccion(sel) or direccion(texto)
        lin = linea(f"{sel} {texto}")
        if dir_ is None or lin is None:
            return None
        return _por_direccion(juegos_h + juegos_a, lin, dir_)

    # Primer set.
    if re.search(r"\b(primer set|1er set|primera manga|first set)\b", texto):
        if incompleto or lado is None:
            return None
        gano_local = ph[0] > pa[0]
        return ACIERTO if (gano_local and lado == "home") or ((not gano_local) and lado == "away") else FALLO

    return None


__all__ = [
    "resolver_mercado", "resolver_total", "lado_en_texto", "metricas_stats",
    "direccion", "linea", "equipo_en_texto", "norm",
    "ACIERTO", "FALLO", "ANULADO",
]


def direccion(texto: str):
    """'over' / 'under'. UNDERS PRIMERO (el bug historico)."""
    t = norm(texto)
    if "menos de" in t or re.search(r"\bunder\b", t) or re.search(r"\bu\s*[0-9]", t):
        return "under"
    if "mas de" in t or re.search(r"\bover\b", t) or re.search(r"\bo\s*[0-9]", t):
        return "over"
    return None


def linea(texto: str):
    """Linea numerica del mercado ('Under 2.5' -> 2.5; '(+1.5)' -> 1.5)."""
    t = norm(texto)
    m = re.search(
        r"(?:over|under|mas de|menos de|o/u|handicap|spread|linea)"
        r"\s*\+?\s*([0-9]+(?:[.,][0-9]+)?)",
        t,
    )
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"\(\s*([+-]?[0-9]+(?:[.,][0-9]+)?)\s*\)", t)
    if m:
        return float(m.group(1).replace(",", "."))
    return None
