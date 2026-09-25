"""Cuotas de DORADOBET (sportsbook Altenar via biahosted.com) — scraper publico.

Endpoints (sin auth):
  GetEvents?sportId=<id>     -> eventos + markets + odds de todo el deporte
  GetEventDetails?eventId=X  -> detalle completo de un evento

Estructura Altenar:
  events[]      : {id, name, competitorIds[], marketIds[], startDate, sportId, champId}
  competitors[] : {id, name}
  markets[]     : {id, name, oddIds: [[...], ...]}
  odds[]        : {id, price, name, typeId (1=local,2=empate,3=visitante), sv (linea)}

SportIds conocidos: soccer=66, baseball(mlb)=76.
"""
import re
from datetime import datetime, timedelta

import requests

DORADO_WIDGET = "https://sb2frontend-altenar2.biahosted.com/api/widget"
DORADO_PARAMS = (
    "culture=es-ES&timezoneOffset=-60&integration=doradobet&deviceType=1"
    "&numFormat=en-GB&countryCode=AT"
)
DORADO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://doradobet.com",
    "Referer": "https://doradobet.com/",
}
DORADO_SPORT_IDS = {"soccer": 66, "mlb": 76}
_DORADO_CACHE = {}
_DORADO_CACHE_TTL = 600  # 10 min (el payload de un deporte pesa >100 KB)
_dorado_bloqueado_hasta = 0
_DORADO_BACKOFF = 300


def _norm(s: str) -> str:
    s = (s or "").lower().replace("b�lgica", "belgica").replace("b�lgium", "belgium")
    s = s.encode("ascii", "ignore").decode("ascii")
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
        "�": "", "b�lgica": "belgica", "b�lgium": "belgium", "italia": "italia",
    }
    for a, b in reemplazos.items():
        s = s.replace(a, b)
    return " ".join(s.replace("\t", " ").split())


def _headers():
    return dict(DORADO_HEADERS)


def get_events_deporte(sport: str):
    """Payload completo de GetEvents para el deporte (con cache)."""
    import time

    global _dorado_bloqueado_hasta
    sport_id = DORADO_SPORT_IDS.get(sport)
    if not sport_id:
        return None
    ahora = time.time()
    if ahora < _dorado_bloqueado_hasta:
        return None
    cached = _DORADO_CACHE.get(sport_id)
    if cached and ahora - cached[0] < _DORADO_CACHE_TTL:
        return cached[1]
    try:
        r = requests.get(
            f"{DORADO_WIDGET}/GetEvents?{DORADO_PARAMS}&sportId={sport_id}",
            headers=_headers(),
            timeout=25,
        )
        if r.status_code in (403, 429):
            _dorado_bloqueado_hasta = ahora + _DORADO_BACKOFF
            return None
        if not r.ok:
            return None
        data = r.json()
        _DORADO_CACHE[sport_id] = (ahora, data)
        return data
    except Exception:
        return None


def _encontrar_evento(data, home_name: str, away_name: str, event_date):
    """Evento cuyo PAR de competidores coincide con los equipos del pick."""
    comps = {c.get("id"): c for c in (data.get("competitors") or [])}
    h = _norm(home_name)
    a = _norm(away_name)
    if not h or not a:
        return None

    def _coincide(nOMBRE_competidor: str, nombre_pick: str) -> bool:
        n = _norm(nOMBRE_competidor)
        return bool(n) and (n in nombre_pick or nombre_pick in n)

    fecha_pick = None
    if event_date:
        try:
            fecha_pick = datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            fecha_pick = None

    for ev in data.get("events") or []:
        ids = ev.get("competitorIds") or []
        if len(ids) < 2:
            continue
        n0 = comps.get(ids[0], {}).get("name") or ""
        n1 = comps.get(ids[1], {}).get("name") or ""
        ok = (_coincide(n0, h) and _coincide(n1, a)) or (
            _coincide(n0, a) and _coincide(n1, h)
        )
        if not ok:
            continue
        try:
            fe = datetime.fromisoformat(str(ev.get("startDate")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if fecha_pick and abs((fe - fecha_pick).total_seconds()) > 12 * 3600:
            continue  # mismo duelo, otra fecha
        return ev
    return None


def _odds_index(data):
    return {o.get("id"): o for o in (data.get("odds") or [])}


def _markets_de_evento(data, ev, filtro=None):
    """Markets del evento (por marketIds), opcionalmente filtrados por nombre."""
    indices = set(ev.get("marketIds") or [])
    salida = []
    for m in data.get("markets") or []:
        if m.get("id") not in indices:
            continue
        if filtro and not filtro((m.get("name") or "").lower()):
            continue
        salida.append(m)
    return salida


def _precio(odd):
    try:
        p = float(odd.get("price"))
    except (TypeError, ValueError):
        return None
    return p if p > 1.0 else None


def _odds_de_market(m, odds_idx):
    """Aplana oddIds ([[a],[b]] o [[a,b],[c,d]]) a la lista de odds reales."""
    salida = []
    for grupo in m.get("oddIds") or []:
        if isinstance(grupo, list):
            for oid in grupo:
                o = odds_idx.get(oid)
                if o:
                    salida.append(o)
        else:
            o = odds_idx.get(grupo)
            if o:
                salida.append(o)
    return salida


def _linea_de(texto: str):
    m = re.search(r"([+-]?\d+(?:[.,]\d+)?)", texto or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


_DORADO_DETALLES = {}


def _detalle_completo(event_id):
    """GetEventDetails: TODOS los mercados y lineas de un evento (cache)."""
    import time

    if event_id in _DORADO_DETALLES:
        return _DORADO_DETALLES[event_id]
    try:
        r = requests.get(
            f"{DORADO_WIDGET}/GetEventDetails?{DORADO_PARAMS}&eventId={event_id}",
            headers=_headers(),
            timeout=30,
        )
        if not r.ok:
            return None
        data = r.json()
        _DORADO_DETALLES[event_id] = data
        return data
    except Exception:
        return None


def cuota_doradobet(sport: str, home_name: str, away_name: str,
                    market: str, selection: str, titulo: str):
    """Cuota REAL de DORADOBET para el mercado/seleccion del pick.

    Devuelve float o None si Doradobet no tiene ese mercado/linea.
    """
    texto = f"{market} {titulo} {selection}".lower()
    nh, na = _norm(home_name), _norm(away_name)
    lado_txt = _norm(f"{titulo} {selection}")

    data = get_events_deporte(sport)
    if not data:
        return None
    ev = _encontrar_evento(data, home_name, away_name, None)
    if not ev:
        return None
    odds_idx = _odds_index(data)

    def _mercado(*claves):
        for m in _markets_de_evento(data, ev):
            n = (m.get("name") or "").lower()
            if any(c in n for c in claves):
                return m
        return None

    def _lado():
        for nombre, lado in ((nh, "home"), (na, "away")):
            if not nombre:
                continue
            if nombre in lado_txt:
                return lado
            palabras = nombre.split()
            if len(palabras) >= 2 and " ".join(palabras[:2]) in lado_txt:
                return lado
        if "empate" in lado_txt or "draw" in lado_txt:
            return "draw"
        return None

    def _odd_name(o):
        return _norm(o.get("name") or "")

    # ---- 1X2 / Ganador ----
    if any(k in texto for k in ("1x2", "ganador", "moneyline", " ml", "cualquier equipo gana")):
        m = _mercado("1x2", "match result", "ganador")
        tipo_esperado = {"home": 1, "draw": 2, "away": 3}.get(_lado())
        for o in _odds_de_market(m or {}, odds_idx):
            if tipo_esperado and o.get("typeId") == tipo_esperado:
                p = _precio(o)
                if p:
                    return p
        return None

    # ---- Apuesta sin empate (Draw No Bet) ----
    if "sin empate" in texto:
        m = _mercado("sin empate", "empate no", "draw no bet")
        lado = _lado()
        for o in _odds_de_market(m or {}, odds_idx):
            n = _odd_name(o)
            if lado == "home" and (n in ("1", "home") or nh in n):
                p = _precio(o)
                if p:
                    return p
            if lado == "away" and (n in ("2", "away") or na in n):
                p = _precio(o)
                if p:
                    return p
        return None

    # ---- Doble oportunidad ----
    if "doble oportunidad" in texto or "doble" in texto:
        m = _mercado("doble oportunidad")
        clave = None
        for k in ("1x", "12", "x2"):
            if k in lado_txt.replace(" ", ""):
                clave = k
                break
        tipo_esperado = {"1x": 9, "12": 10, "x2": 11}.get(clave)
        for o in _odds_de_market(m or {}, odds_idx):
            if tipo_esperado and o.get("typeId") == tipo_esperado:
                p = _precio(o)
                if p:
                    return p
            n = _odd_name(o).replace(" ", "")
            if clave and n == clave:
                p = _precio(o)
                if p:
                    return p
        return None

    # ---- Handicap ----
    if "handicap" in texto:
        lado = _lado()
        linea = _linea_de(str(selection))
        for m in _markets_de_evento(data, ev):
            n = (m.get("name") or "").lower()
            if "handicap" not in n:
                continue
            for o in _odds_de_market(m, odds_idx):
                try:
                    sv = float(o.get("sv"))
                except (TypeError, ValueError):
                    continue
                if lado and linea is not None:
                    # sv de Altenar se expresa sobre el local
                    esperado = linea if lado == "home" else -linea
                    if abs(sv - esperado) > 0.26:
                        continue
                    p = _precio(o)
                    if p:
                        return p
        return None

    # ---- Over/Under (goles, carreras, corners, tarjetas) ----
    if any(k in texto for k in ("over", "under", "total", "mas de", "menos de")):
        es_corner = "corner" in texto
        es_tarjeta = "tarjeta" in texto or "card" in texto
        es_carrera = "carrera" in texto or "run" in texto
        es_over = any(k in texto for k in ("over", "mas de"))
        linea = _linea_de(str(selection))
        lado = _lado()
        nombre_equipo = nh if lado == "home" else (na if lado == "away" else "")
        if nombre_equipo:
            nombre_equipo = " ".join(nombre_equipo.split()[:2])

        def _filtro(nm):
            if es_corner and "corner" not in nm:
                return False
            if es_tarjeta and "tarjeta" not in nm and "card" not in nm:
                return False
            if es_carrera and not any(k in nm for k in ("carrera", "run", "total")):
                return False
            return True

        mercados = _markets_de_evento(data, ev, _filtro)
        # team total del equipo mencionado tiene prioridad
        if nombre_equipo:
            team_markets = [m for m in mercados if nombre_equipo in (m.get("name") or "").lower()]
            otros = [m for m in mercados if m not in team_markets]
            mercados = team_markets + otros
        for m in mercados:
            for o in _odds_de_market(m, odds_idx):
                nombre_odd = _odd_name(o)
                es_over_odd = "mas de" in nombre_odd or nombre_odd.startswith("over")
                es_under_odd = "menos de" in nombre_odd or nombre_odd.startswith("under")
                if es_over and not es_over_odd:
                    continue
                if not es_over and not es_under_odd:
                    continue
                sv = None
                try:
                    sv = float(o.get("sv"))
                except (TypeError, ValueError):
                    sv = _linea_de(nombre_odd)
                if linea is not None and sv is not None and abs(sv - linea) > 0.01:
                    continue
                p = _precio(o)
                if p:
                    return p

        # linea no encontrada en el payload ligero: buscar en el DETALLE
        # completo (GetEventDetails trae todas las lineas del mercado)
        detalle = _detalle_completo(ev.get("id"))
        if not detalle:
            return None
        detalle_idx = _odds_index(detalle)
        for m in detalle.get("markets") or []:
            nm = (m.get("name") or "").lower()
            if not any(k in nm for k in ("total", "goles", "carrera", "corner", "tarjeta", "mas", "menos")):
                continue
            if es_corner and "corner" not in nm:
                continue
            if es_tarjeta and "tarjeta" not in nm and "card" not in nm:
                continue
            for o in _odds_de_market(m, detalle_idx):
                nombre_odd = _norm(o.get("name") or "")
                es_over_odd = "mas de" in nombre_odd or nombre_odd.startswith("over")
                es_under_odd = "menos de" in nombre_odd or nombre_odd.startswith("under")
                if es_over and not es_over_odd:
                    continue
                if not es_over and not es_under_odd:
                    continue
                sv = None
                try:
                    sv = float(o.get("sv"))
                except (TypeError, ValueError):
                    sv = _linea_de(nombre_odd)
                if linea is not None and sv is not None and abs(sv - linea) > 0.01:
                    continue
                p = _precio(o)
                if p:
                    return p
        return None

    # ---- Ambos equipos marcan ----
    if "ambos" in texto or "btts" in texto:
        m = _mercado("ambos equipos marcan", "btts", "both teams")
        sel = (selection or "").strip().lower()
        es_si = sel in ("si", "sí", "yes") or "si" in lado_txt.split()
        for o in _odds_de_market(m or {}, odds_idx):
            n = _odd_name(o)
            if es_si and n in ("si", "sí", "yes"):
                p = _precio(o)
                if p:
                    return p
            if not es_si and n == "no":
                p = _precio(o)
                if p:
                    return p
        return None

    return None



def event_id_doradobet(sport, home_name, away_name):
    """Resuelve el eventId de Doradobet por nombres de equipos."""
    data = get_events_deporte(sport)
    if not data:
        return None
    ev = _encontrar_evento(data, home_name, away_name, None)
    if ev:
        return str(ev.get("id"))
    # Fallback tolerante a la codificación dañada que devuelve el widget.
    def simple(value):
        return re.sub(r"[^a-z]", "", str(value or "").encode("ascii", "ignore").decode().lower())
    h, a = simple(home_name), simple(away_name)
    comps = {c.get("id"): c.get("name") for c in data.get("competitors") or []}
    for event in data.get("events") or []:
        names = [simple(comps.get(i)) for i in event.get("competitorIds") or []]
        if {h, a} == set(names) and (h and a):
            return str(event.get("id"))
    return None



def detalle_mercado_para_ia(event_id, limite_odds=1000):
    """Normaliza GetEventDetails (incluidos childMarkets) para el prompt de la IA."""
    data = _detalle_completo(event_id) or {}
    odds = {o.get("id"): o for o in data.get("odds") or []}
    children = {c.get("id"): c for c in data.get("childMarkets") or []}
    filas = []
    for mercado in data.get("markets") or []:
        nombre = (mercado.get("name") or "").strip()
        if not nombre:
            continue
        objetivos = [children.get(mid) for mid in mercado.get("childMarketIds") or []]
        objetivos = [c for c in objetivos if c]
        if not objetivos:
            objetivos = [{"desktopOddIds": mercado.get("desktopOddIds") or []}]
        for hijo in objetivos:
            etiqueta = (hijo.get("name") or hijo.get("shortName") or nombre).strip()
            for grupo in hijo.get("desktopOddIds") or []:
                for oid in (grupo if isinstance(grupo, list) else [grupo]):
                    odd = odds.get(oid) or {}
                    precio = _precio(odd)
                    if not precio:
                        continue
                    sel = (odd.get("name") or odd.get("sv") or "").strip()
                    filas.append((nombre, etiqueta, sel, precio, odd.get("sv")))
                    if len(filas) >= limite_odds:
                        break
                if len(filas) >= limite_odds:
                    break
            if len(filas) >= limite_odds:
                break
        if len(filas) >= limite_odds:
            break
    if not filas:
        return ""
    lineas = ["MERCADOS Y CUOTAS DORADOBET (incluye props de jugadores):"]
    for mercado, hijo, seleccion, precio, linea in filas:
        sufijo = f" linea={linea}" if linea and linea != "0.5" else ""
        lineas.append(f"- {mercado} | {hijo} | {seleccion}{sufijo} | cuota={precio:g}")
    return "\n".join(lineas)
