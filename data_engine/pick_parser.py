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
    if t in ("1", "local", "home"):
        return "home"
    if t in ("2", "visitante", "away"):
        return "away"
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

    # Mercados SIN datos gratis: primer juego/punto de servicio (tenis)
    if "primer juego" in txt or "primer punto" in txt:
        return None

    # ---- MULTIGOLES (rango) ----
    if "multigoles" in txt:
        m = re.search(r'(\d+)\s*-\s*(\d+)', sel_raw or "")
        if m:
            return ("multigoles", f"{m.group(1)}-{m.group(2)}", None)
        return None

    lado = _lado(sel_raw, home, away)

    # linea over/under: puede venir en el titulo O en la seleccion ('Over 2.5')
    m = re.search(r'(?:mas de|más de|menos de|over|under)\s*([0-9]+(?:[.,][0-9]+)?)',
                  txt + " " + sel)
    linea = float(m.group(1).replace(",", ".")) if m else None
    # handicap tipo (+0.5) o (1:0)
    mh = re.search(r'\(\s*([+-]?[0-9]+(?:[.,][0-9]+)?)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*\)', sel_raw)
    if mh:
        linea = float(mh.group(1).replace(",", ".")) - float(mh.group(2).replace(",", "."))
    else:
        mh2 = re.search(r'\(\s*([+-][0-9]+(?:[.,][0-9]+)?)\s*\)', sel_raw)
        if mh2:
            linea = float(mh2.group(1).replace(",", "."))

    # ---- MERCADOS DE PERIODO (mitades / cuartos) ----
    # Antes TODOS se descartaban aqui como 'sin datos' y JAMAS se verificaban
    # (medido en produccion: 18 'sin_datos' en una sola pasada del panel).
    # ESPN publica el marcador por periodos (linescores), asi que se resuelven
    # con datos reales igual que el marcador final. OJO: un mercado de periodo
    # NUNCA debe caer en las reglas de partido completo (seria resolver con el
    # marcador equivocado): si no se puede interpretar, se devuelve None.
    es_ambas = "ambas mitades" in txt
    es_cualquier = (not es_ambas) and (
        "cualquier mitad" in txt or ("alguna" in txt and "mitad" in txt)
    )
    es_primera = bool(re.search(r"\b1\s*(?:a|ª|er)?\s*(?:mitad|half)\b", txt)) \
        or "primera mitad" in txt or "primer tiempo" in txt
    es_segunda = bool(re.search(r"\b2\s*(?:a|ª|do)?\s*(?:mitad|half)\b", txt)) \
        or "segunda mitad" in txt or "segundo tiempo" in txt
    m_cuarto = re.search(
        r"(primer|1er|1º|segundo|2do|2º|tercer|3er|3º|cuarto|4to|4º)\s*cuarto", txt)
    n_cuarto = {"primer": 1, "1er": 1, "1º": 1, "segundo": 2, "2do": 2, "2º": 2,
                "tercer": 3, "3er": 3, "3º": 3, "cuarto": 4, "4to": 4, "4º": 4
                }.get(m_cuarto.group(1)) if m_cuarto else None

    if es_ambas or es_cualquier or es_primera or es_segunda or n_cuarto:
        # Corners/tarjetas/faltas POR PERIODO: ESPN no los desglosa. Mejor
        # 'sin datos' que resolver con el total del partido (seria mentira).
        if any(k in txt for k in ("corner", "esquina", "tarjeta", "faltas")):
            return None
        # 'Ambos marcan en la 1a mitad': tampoco hay dato por periodo.
        if "ambos" in txt and not es_ambas:
            return None

        if es_ambas or es_cualquier:
            lado_txt = _lado(f"{market} {titulo}", home, away)
            if lado_txt is None and (
                "cualquier equipo" in txt or "algun equipo" in txt or "algún equipo" in txt
            ):
                lado_txt = "cualquiera"
            if lado_txt is None:
                return None
            tipo = "ambas_mitades" if es_ambas else "cualquier_mitad"
            si_no = "no" if sel.startswith("no") else "si"
            return (f"{tipo}_{si_no}", lado_txt, None)

        # indice del periodo: 1/2 para mitades, 1-4 para cuartos
        cual = n_cuarto or (1 if es_primera else 2)
        indice = "cuarto" if n_cuarto else "mitad"

        txt_dir = txt + " " + sel
        direccion = None
        if "mas de" in txt_dir or "más de" in txt_dir or "over" in txt_dir:
            direccion = "over"
        if "menos de" in txt_dir or "under" in txt_dir:
            direccion = "under"

        if direccion:
            if linea is None:
                return None
            lado_txt = _lado(f"{market} {titulo}", home, away)
            if lado_txt:
                return ("team_periodo", f"{lado_txt}|{direccion}|{indice}|{cual}", linea)
            return ("over_under_periodo", f"{direccion}|{indice}|{cual}", linea)

        # 'X gana la primera mitad' / '1a mitad - 1x2' / 'primer cuarto ganador'
        lado_sel = lado or _lado(f"{market} {titulo}", home, away)
        if lado_sel is None:
            return None
        if lado is None and sel.startswith("no"):
            # 'Nacional gana la 1a mitad: NO' -> acierto si NO la gana.
            lado_sel = "no_" + lado_sel
        return ("winner_periodo", f"{lado_sel}|{indice}|{cual}", None)

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
