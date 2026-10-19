"""
Resolutor de picks de la tabla ai_picks usando el motor de datos reales.
"""
import re

def _palabras(s):
    """Palabras significativas de un nombre de equipo."""
    s = re.sub(r'\([^)]*\)', '', (s or "")).strip().lower()
    partes = re.split(r'[\s.\-/]+', s)
    stop = {"", "fc", "cf", "de", "del", "the", "sc", "ac", "afc", "nfc", "club"}
    return {p for p in partes if len(p) > 1 and p not in stop}

def _parecido_equipo(a, b):
    a = re.sub(r'\([^)]*\)', '', (a or "")).strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return bool(_palabras(a) & _palabras(b))

def _lado(sel, home, away):
    """Determina si la seleccion apunta a home / away / draw."""
    t = re.sub(r'\([^)]*\)', '', (sel or "")).strip().lower()
    if _parecido_equipo(t, home):
        return "home"
    if _parecido_equipo(t, away):
        return "away"
    if "empate" in t or t == "x":
        return "draw"
    return None

def parsear_pick(pick):
    """
    Convierte un pick de la BD en (market_type, selection, line_value).
    Devuelve None si el mercado no es resoluble con datos gratuitos.
    """
    market = (pick.get("market") or "").lower()
    titulo = (pick.get("titulo") or "").lower()
    sel_raw = pick.get("selection") or ""
    home = pick.get("home_name") or ""
    away = pick.get("away_name") or ""
    txt = market + " " + titulo
    sel = (sel_raw or "").lower()

    # Mercados SIN datos gratis: 1a/2a mitad, primer punto de servicio
    if any(k in txt for k in ("1ª mitad", "2ª mitad", "1a mitad", "2a mitad", "primer tiempo", "segundo tiempo")):
        return None
    if "primer juego" in txt or "primer punto" in txt:
        return None

    # ---- MULTIGOLES (rango) ----
    if "multigoles" in txt:
        m = re.search(r'(\d+)\s*-\s*(\d+)', sel_raw or "")
        if m:
            return ("multigoles", f"{m.group(1)}-{m.group(2)}", None)
        return None

    lado = _lado(sel_raw, home, away)

    # linea over/under
    m = re.search(r'(?:mas de|más de|menos de|over|under)\s*([0-9]+(?:[.,][0-9]+)?)', txt)
    linea = float(m.group(1).replace(",", ".")) if m else None
    # handicap tipo (+0.5) o (1:0)
    mh = re.search(r'\(\s*([+-]?[0-9]+(?:[.,][0-9]+)?)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*\)', sel_raw)
    if mh:
        linea = float(mh.group(1).replace(",", ".")) - float(mh.group(2).replace(",", "."))
    else:
        mh2 = re.search(r'\(\s*([+-][0-9]+(?:[.,][0-9]+)?)\s*\)', sel_raw)
        if mh2:
            linea = float(mh2.group(1).replace(",", "."))

    # ---- CORNERS ----
    if any(k in txt for k in ("tiros de esquina", "corners", "esquina")):
        if "handicap" in txt or "hándicap" in txt:
            return ("handicap_corners", lado, linea)
        if "1x2" in txt:
            return ("corners_1x2", lado, None)
        if "mas de" in txt or "más de" in txt or "menos de" in txt:
            return ("corners", "over" if ("mas de" in txt or "más de" in txt) else "under", linea)
        return ("corners_1x2", lado, None)

    # ---- REMATES / TIROS ----
    if "remates" in txt or "tiros" in txt or "shotes" in txt:
        if "porteria" in txt or "puerta" in txt:
            return ("shots_on_target_1x2", lado, None)
        return ("shots_1x2", lado, None)

    # ---- FALTAS / TARJETAS ----
    if "faltas" in txt:
        return ("fouls_1x2", lado, None)
    if "tarjeta" in txt or "amarilla" in txt:
        if "handicap" in txt or "hándicap" in txt:
            return ("handicap_cards", lado, linea)
        return ("cards_1x2", lado, None)

    # ---- AMBOS MARCAN ----
    if "ambos marcan" in txt or "ambos equipos marcan" in txt:
        return ("btts", "si" if sel.startswith("si") or sel == "sí" else "no", None)

    # ---- DOBLE OPORTUNIDAD ----
    if "doble oportunidad" in txt:
        en_home = _parecido_equipo(sel, home)
        en_away = _parecido_equipo(sel, away)
        if en_home and en_away:
            return ("double_chance", "12", None)
        if en_home:
            return ("double_chance", "1x", None)
        if en_away:
            return ("double_chance", "x2", None)
        return None

    # ---- OVER / UNDER GOLES ----
    if "over" in txt or "under" in txt or "goles" in txt:
        if "mas de" in txt or "más de" in txt or "over" in txt:
            return ("over_under", "over", linea)
        if "menos de" in txt or "under" in txt:
            return ("over_under", "under", linea)

    # ---- HANDICAP GENERICO ----
    if "handicap" in txt or "hándicap" in txt:
        return ("handicap", lado, linea)

    # ---- GANADOR / 1X2 ----
    if "ganador" in txt or "1x2" in txt or "1 x 2" in txt:
        return ("winner", lado, None)

    return None
