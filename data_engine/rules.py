"""
MOTOR DE VERIFICACION: decide ACIERTO/FALLO para cualquier mercado
usando los datos completos del partido (ESPN).
"""
import re
from typing import Dict, Any, Optional

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _ok(acierto: bool, value, conf: float) -> Dict[str, Any]:
    return {"result": "ACIERTO" if acierto else "FALLO",
            "value_real": _f(value),
            "confidence": conf}

def _no() -> Dict[str, Any]:
    return {"result": "NO_RESOLUBLE", "value_real": None, "confidence": 0}

def _over_under(sel: str, valor: Optional[float], linea: Optional[float]) -> Optional[bool]:
    if valor is None or linea is None:
        return None
    if sel in ("over", "mas de", "masde", "+"):
        return valor > linea
    if sel in ("under", "menos de", "menosde", "-"):
        return valor < linea
    return None


def _periodos(d: Dict):
    """Marcador por periodo (linescores de ESPN): (home, away) o (None, None)."""
    hp = [v for v in (d.get("home_periodos") or []) if v is not None]
    ap = [v for v in (d.get("away_periodos") or []) if v is not None]
    if not hp or not ap:
        return None, None
    n = min(len(hp), len(ap))
    if n == 0:
        return None, None
    return hp[:n], ap[:n]


def _grupos_mitad(hp, ap):
    """Agrupa los periodos en MITADES.

    Futbol: 2 periodos = 2 mitades (un 3er periodo seria prorroga: no cuenta).
    NBA/NFL: 4+ cuartos -> cada mitad es la suma de sus cuartos.
    """
    n = len(hp)
    if n >= 4:
        m = n // 2
        return [(sum(hp[:m]), sum(ap[:m])), (sum(hp[m:m * 2]), sum(ap[m:m * 2]))]
    if n >= 2:
        return [(hp[0], ap[0]), (hp[1], ap[1])]
    return []


def _scores_periodo(d: Dict, indice: str, cual: int):
    """Goles/puntos de UN periodo: ('mitad', 1|2) o ('cuarto', 1-4)."""
    hp, ap = _periodos(d)
    if hp is None:
        return None, None
    if indice == "mitad":
        grupos = _grupos_mitad(hp, ap)
        if cual < 1 or cual > len(grupos):
            return None, None
        return grupos[cual - 1]
    # cuarto: periodo individual
    if cual < 1 or cual > len(hp):
        return None, None
    return hp[cual - 1], ap[cual - 1]

def resolver_mercado(market_type: str, d: Dict, selection: str, line_value=None) -> Dict[str, Any]:
    mt = _norm(market_type)
    sel = _norm(selection)
    lv = _f(line_value)
    home = _f(d.get("home_score"))
    away = _f(d.get("away_score"))

    # ---- GANADOR / 1X2 ----
    if mt in ("winner", "1x2", "ganador", "match_winner", "moneyline"):
        if home is None or away is None:
            return _no()
        real = "home" if home > away else ("away" if away > home else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, home + away, 0.95)

    # ---- DOBLE OPORTUNIDAD ----
    if mt in ("double_chance", "doble oportunidad"):
        if home is None or away is None:
            return _no()
        if sel in ("1x", "home_or_draw", "local o empate"):
            acierto = home >= away
        elif sel in ("x2", "draw_or_away", "empate o visitante"):
            acierto = away >= home
        elif sel == "12":
            acierto = home != away
        else:
            return _no()
        return _ok(acierto, home + away, 0.9)

    # ---- EMPATE NO VALE ----
    if mt in ("draw_no_bet", "sin empate", "dnb"):
        if home is None or away is None:
            return _no()
        if home == away:
            return {"result": "FALLO", "value_real": home + away, "confidence": 0.9}
        acierto = (sel in ("home", "1", "local") and home > away) or \
                  (sel in ("away", "2", "visitante") and away > home)
        return _ok(acierto, home + away, 0.9)

    # ---- OVER / UNDER GOLES ----
    if mt in ("over_under", "over_under_goles", "total_goles", "goals"):
        total = _f(d.get("total_goals"))
        if total is None and home is not None and away is not None:
            total = home + away
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.9)

    # ---- GOLES DE UN EQUIPO ----
    if mt in ("team_goals", "goles equipo", "equipo total de goles"):
        valor = home if sel.startswith("home") else away
        limpio = sel.replace("home", "").replace("away", "").strip()
        r = _over_under(limpio, valor, lv)
        return _no() if r is None else _ok(r, valor, 0.85)

    # ---- AMBOS MARCAN ----
    if mt in ("btts", "ambos marcan", "both_teams_score"):
        if home is None or away is None:
            return _no()
        ambos = home > 0 and away > 0
        acierto = (sel in ("si", "sí", "yes", "true", "1") and ambos) or \
                  (sel in ("no", "false", "0") and not ambos)
        return _ok(acierto, home + away, 0.9)

    # ---- CORNERS ----
    if mt in ("corners", "tiros de esquina", "esquinas", "total_corners"):
        total = _f(d.get("total_corners"))
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.85)

    if mt in ("corners_home", "corners local"):
        r = _over_under(sel, _f(d.get("home_corners")), lv)
        return _no() if r is None else _ok(r, _f(d.get("home_corners")), 0.85)

    if mt in ("corners_away", "corners visitante"):
        r = _over_under(sel, _f(d.get("away_corners")), lv)
        return _no() if r is None else _ok(r, _f(d.get("away_corners")), 0.85)

    if mt in ("corners_1h", "corners primera mitad", "corners 1a mitad"):
        ch = _f(d.get("home_corners_1h")); ca = _f(d.get("away_corners_1h"))
        total = (ch + ca) if ch is not None and ca is not None else None
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.6)

    # ---- TARJETAS ----
    if mt in ("cards", "tarjetas", "total_cards", "tarjetas totales"):
        hy = _f(d.get("home_yellow")); ay = _f(d.get("away_yellow"))
        hr = _f(d.get("home_red")); ar = _f(d.get("away_red"))
        if None in (hy, ay, hr, ar):
            return _no()
        total = hy + ay + hr + ar
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.8)

    if mt in ("yellow_cards", "amarillas", "tarjetas amarillas"):
        hy = _f(d.get("home_yellow")); ay = _f(d.get("away_yellow"))
        total = (hy + ay) if hy is not None and ay is not None else None
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.85)

    # ---- FALTAS ----
    if mt in ("fouls", "faltas", "total_fouls"):
        total = _f(d.get("total_fouls"))
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.8)

    # ---- TIROS A PUERTA ----
    if mt in ("shots_on_target", "tiros a puerta"):
        hs = _f(d.get("home_shots_on_target")); as_ = _f(d.get("away_shots_on_target"))
        total = (hs + as_) if hs is not None and as_ is not None else None
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.75)

    if mt in ("shots_on_target_home", "tiros a puerta local"):
        r = _over_under(sel, _f(d.get("home_shots_on_target")), lv)
        return _no() if r is None else _ok(r, _f(d.get("home_shots_on_target")), 0.75)

    # ---- ATAJADAS ----
    if mt in ("saves", "atajadas"):
        hs = _f(d.get("home_saves")); as_ = _f(d.get("away_saves"))
        total = (hs + as_) if hs is not None and as_ is not None else None
        r = _over_under(sel, total, lv)
        return _no() if r is None else _ok(r, total, 0.75)

    # ---- HANDICAP ----
    if mt in ("handicap", "handicap_1x2"):
        if home is None or away is None or lv is None:
            return _no()
        if sel in ("home", "1", "local"):
            acierto = (home + lv) > away
        elif sel in ("away", "2", "visitante"):
            acierto = (away + lv) > home
        else:
            return _no()
        return _ok(acierto, home + away, 0.8)

    # ---- JUGADOR: PUNTOS / REBOTES / ASISTENCIAS ----
    if mt in ("player_points", "puntos jugador"):
        r = _over_under(sel, _f(d.get("player_points")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_points")), 0.9)

    if mt in ("player_rebounds", "rebotes jugador"):
        r = _over_under(sel, _f(d.get("player_rebounds")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_rebounds")), 0.9)

    if mt in ("player_assists", "asistencias jugador"):
        r = _over_under(sel, _f(d.get("player_assists")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_assists")), 0.9)

    # ---- NFL: PASES DE TD ----
    if mt in ("player_td_passes", "pases de td jugador"):
        r = _over_under(sel, _f(d.get("player_td_passes")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_td_passes")), 0.9)

    # ---- TENIS: ACES / DOBLES FALTAS ----
    if mt in ("aces", "servicios directos"):
        r = _over_under(sel, _f(d.get("player_aces")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_aces")), 0.7)

    if mt in ("double_faults", "dobles faltas"):
        r = _over_under(sel, _f(d.get("player_double_faults")), lv)
        return _no() if r is None else _ok(r, _f(d.get("player_double_faults")), 0.7)

    # ---- 1X2 DE STATS (que equipo tiene mas) ----
    if mt in ("corners_1x2", "1x2 corners"):
        hc = _f(d.get("home_corners")); ac = _f(d.get("away_corners"))
        if hc is None or ac is None:
            return _no()
        real = "home" if hc > ac else ("away" if ac > hc else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, hc + ac, 0.85)

    if mt in ("shots_1x2", "1x2 remates totales", "remates_1x2"):
        hs = _f(d.get("home_shots")); as_ = _f(d.get("away_shots"))
        if hs is None or as_ is None:
            return _no()
        real = "home" if hs > as_ else ("away" if as_ > hs else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, hs + as_, 0.75)

    if mt in ("shots_on_target_1x2", "1x2 remates a porteria", "remates_puerta_1x2"):
        hs = _f(d.get("home_shots_on_target")); as_ = _f(d.get("away_shots_on_target"))
        if hs is None or as_ is None:
            return _no()
        real = "home" if hs > as_ else ("away" if as_ > hs else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, hs + as_, 0.75)

    if mt in ("fouls_1x2", "1x2 faltas"):
        hf = _f(d.get("home_fouls")); af = _f(d.get("away_fouls"))
        if hf is None or af is None:
            return _no()
        real = "home" if hf > af else ("away" if af > hf else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, hf + af, 0.8)

    if mt in ("cards_1x2", "1x2 tarjetas"):
        hy = _f(d.get("home_yellow")); ay = _f(d.get("away_yellow"))
        hr = _f(d.get("home_red")); ar = _f(d.get("away_red"))
        if None in (hy, ay, hr, ar):
            return _no()
        hc = hy + hr; ac = ay + ar
        real = "home" if hc > ac else ("away" if ac > hc else "draw")
        acierto = (sel in ("home", "1", "local") and real == "home") or \
                  (sel in ("away", "2", "visitante") and real == "away") or \
                  (sel in ("draw", "empate", "x") and real == "draw")
        return _ok(acierto, hc + ac, 0.8)

    if mt in ("handicap_corners", "handicap tiros de esquina"):
        hc = _f(d.get("home_corners")); ac = _f(d.get("away_corners"))
        if hc is None or ac is None or lv is None:
            return _no()
        if sel in ("home", "1", "local"):
            acierto = (hc + lv) > ac
        elif sel in ("away", "2", "visitante"):
            acierto = (ac + lv) > hc
        else:
            return _no()
        return _ok(acierto, hc + ac, 0.75)

    # ---- MULTIGOLES (rango de goles) ----
    if mt in ("multigoles", "rango goles"):
        total = _f(d.get("total_goals"))
        if total is None and home is not None and away is not None:
            total = home + away
        m = re.match(r'\s*(\d+)\s*-\s*(\d+)\s*', selection or "")
        if total is None or not m:
            return _no()
        lo, hi = int(m.group(1)), int(m.group(2))
        return _ok(lo <= total <= hi, total, 0.85)

    # ---- HANDICAP GENERICO (1x2 con handicap) ----
    if mt in ("handicap", "handicap_1x2"):
        if home is None or away is None or lv is None:
            return _no()
        if sel in ("home", "1", "local"):
            acierto = (home + lv) > away
        elif sel in ("away", "2", "visitante"):
            acierto = (away + lv) > home
        else:
            return _no()
        return _ok(acierto, home + away, 0.85)

    # ---- HANDICAP DE TARJETAS ----
    if mt in ("handicap_cards", "handicap tarjetas"):
        hy = _f(d.get("home_yellow")); ay = _f(d.get("away_yellow"))
        hr = _f(d.get("home_red")); ar = _f(d.get("away_red"))
        if None in (hy, ay, hr, ar) or lv is None:
            return _no()
        hc = hy + hr; ac = ay + ar
        if sel in ("home", "1", "local"):
            acierto = (hc + lv) > ac
        elif sel in ("away", "2", "visitante"):
            acierto = (ac + lv) > hc
        else:
            return _no()
        return _ok(acierto, hc + ac, 0.8)

    # ---- MERCADOS DE PERIODO (mitades / cuartos) ----
    # El pick_parser codifica el periodo en la seleccion: '<...>|<mitad|cuarto>|<n>'.
    if mt == "winner_periodo":
        try:
            lado_p, indice, cual = sel.split("|")
            cual = int(cual)
        except (ValueError, AttributeError):
            return _no()
        hp, ap = _scores_periodo(d, indice, cual)
        if hp is None:
            return _no()
        real = "home" if hp > ap else ("away" if ap > hp else "draw")
        invertir = lado_p.startswith("no_")
        if invertir:
            lado_p = lado_p[3:]
        base = (lado_p in ("home", "1", "local") and real == "home") or \
               (lado_p in ("away", "2", "visitante") and real == "away") or \
               (lado_p in ("draw", "empate", "x") and real == "draw")
        return _ok((not base) if invertir else base, hp + ap, 0.9)

    if mt in ("cualquier_mitad_si", "cualquier_mitad_no",
              "ambas_mitades_si", "ambas_mitades_no"):
        hp, ap = _periodos(d)
        if hp is None:
            return _no()
        mitades = _grupos_mitad(hp, ap)
        if not mitades:
            return _no()
        todas = mt.startswith("ambas")
        if sel in ("home", "1", "local"):
            gana = [h > a for h, a in mitades]
        elif sel in ("away", "2", "visitante"):
            gana = [a > h for h, a in mitades]
        elif sel == "cualquiera" and not todas:
            # 'Cualquier equipo gana alguna mitad': basta una mitad NO empatada.
            gana = [h != a for h, a in mitades]
        else:
            return _no()
        resultado = all(gana) if todas else any(gana)
        acierto = resultado if mt.endswith("_si") else (not resultado)
        return _ok(acierto, sum(h + a for h, a in mitades), 0.9)

    if mt in ("over_under_periodo", "team_periodo"):
        partes = sel.split("|")
        if mt == "over_under_periodo":
            if len(partes) != 3:
                return _no()
            direccion, indice, cual = partes
            lado_p = None
        else:
            if len(partes) != 4:
                return _no()
            lado_p, direccion, indice, cual = partes
        try:
            cual = int(cual)
        except ValueError:
            return _no()
        hp, ap = _scores_periodo(d, indice, cual)
        if hp is None:
            return _no()
        if lado_p in ("home", "1", "local"):
            valor = hp
        elif lado_p in ("away", "2", "visitante"):
            valor = ap
        elif lado_p is None:
            valor = hp + ap
        else:
            return _no()
        r = _over_under(direccion, valor, lv)
        return _no() if r is None else _ok(r, valor, 0.85)

    return _no()

