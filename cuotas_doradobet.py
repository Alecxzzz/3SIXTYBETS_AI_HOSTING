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
# SportIds verificados uno por uno contra GetEvents (no deducidos): cada
# sportId trae 'sport' y nombre de evento, asi que se pudo confirmar cual es
# cual mirando los equipos, no de memoria.
DORADO_SPORT_IDS = {
    "soccer": 66,
    "nba": 67,     # Detroit Pistons / Boston Celtics
    "mlb": 76,     # ATL Braves / PHI Phillies
    "nfl": 75,     # CLE Browns / PIT Steelers
    "tennis": 68,  # Alejandro Tabilo / Tommy Paul  (ATP y WTA de verdad)
    "hockey": 70,  # PHI Flyers / PIT Penguins
}
# El tenis esta repartido en varios sportIds: el 68 es el circuito mayor (ATP y
# WTA, 386 eventos) y el 77/78 son Challenger e ITF. Antes solo se usaba el
# 77/78, por eso NO se encontraba ningun tenista de la ventana de ESPN y se
# concluyo (mal) que el bookmaker no cubria el tenis.
DORADO_SPORT_IDS_LIST = {
    "soccer": [66],
    "nba": [67],
    "mlb": [76],
    "nfl": [75],
    "hockey": [70],
    "tennis": [68, 77, 78],
}
_DORADO_CACHE = {}
_DORADO_CACHE_TTL = 600  # 10 min (el payload de un deporte pesa >100 KB)
_dorado_bloqueado_hasta = 0
_DORADO_BACKOFF = 300


def _norm(s: str) -> str:
    s = (s or "").lower().replace("b�lgica", "belgica").replace("b�lgium", "belgium")
    # El bookmaker a veces devuelve los nombres con tabs y espacios de relleno
    # ('WAS Commanders\t\t'). Sin limpiarlos, ningun equipo de NFL emparejaba
    # con su evento.
    s = " ".join(str(s or "").split())
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


def _get_events_sport_id(sport_id: int):
    """Payload de GetEvents para un sportId concreto (con cache y backoff)."""
    global _dorado_bloqueado_hasta
    import time

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


def get_events_deporte(sport: str):
    """Payload de GetEvents para el deporte, uniendo TODOS sus sportIds.

    Un deporte puede estar repartido en varios sportIds (el tenis usa el 68
    para ATP/WTA y el 77/78 para Challenger/ITF). Devolver solo el primero
    hacia que los tenistas del Challenger si se encontraran pero los del
    circuito mayor no: por eso el tenista no encontraba su evento. Aqui se
    combinan los eventos de todos los ids en un unico payload.
    """
    ids = DORADO_SPORT_IDS_LIST.get(sport)
    if not ids:
        return None
    partes = []
    for sport_id in ids:
        data = _get_events_sport_id(sport_id)
        if data:
            partes.append(data)
    if not partes:
        return None
    if len(partes) == 1:
        return partes[0]
    # Fusion: se acumulan eventos, competidores, mercados y odds de cada id.
    # Los ids de entidad son globales en el provider, asi que no colisionan.
    combinado = {"events": [], "competitors": [], "markets": [], "odds": []}
    vistos_ev = set()
    for d in partes:
        for e in d.get("events") or []:
            if e.get("id") in vistos_ev:
                continue
            vistos_ev.add(e.get("id"))
            combinado["events"].append(e)
        for clave in ("competitors", "markets", "odds"):
            combinado[clave].extend(d.get(clave) or [])
    return combinado


def _coincide_equipo(nombre_book: str, nombre_pick: str) -> bool:
    """True si dos nombres de equipo/tenista son el mismo.

    El sportsbook usa abreviaturas ('ATL Braves', 'HOU Astros') y ESPN el
    nombre completo ('Atlanta Braves'), asi que comparar con '=' no basta.
    Se comparan los apellidos (ultima palabra con 3+ letras) y el prefijo de
    la sigla, que es lo unico estable entre ambas fuentes.
    """
    a, b = _norm(nombre_book), _norm(nombre_pick)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    pa = [w for w in a.replace(".", " ").split() if len(w) >= 3]
    pb = [w for w in b.replace(".", " ").split() if len(w) >= 3]
    if not pa or not pb:
        return False
    # Ultima palabra = apellido/sobrenombre (Jirator, Hrbaty, Braves...).
    if pa[-1] == pb[-1]:
        return True
    # Tenis: el bookmaker escribe 'Khachanov K' (apellido + inicial) y ESPN
    # 'Karen Khachanov' (nombre completo). El apellido es la palabra larga
    # (>=4 letras) que coincide entre ambos; la inicial de un solo caracter se
    # ignora. Sin esto, ningun tenista del circuito mayor encontraba su evento.
    if len(pa[-1]) >= 4 and pa[-1] == pb[-1]:
        return True
    if len(pa[0]) >= 4 and pa[0] == pb[-1]:
        return True
    if len(pb[0]) >= 4 and pb[0] == pa[-1]:
        return True
    # Sigla de 3 letras al principio: 'atl braves' vs 'atlanta braves'.
    if pa[0] == pb[0] and len(pa[0]) == 3:
        return True
    # Alias frecuentes entre las dos fuentes.
    alias = {
        "atletico": "atm", "athletic": "ath", "athletic bilbao": "ath",
        "real sociedad": "rso", "real madrid": "rma", "atletico madrid": "atm",
        "bayern": "fc bayern", "bayern munchen": "fc bayern munich",
        "dortmund": "borussia", "bvb": "borussia dortmund",
        "inter": "internazionale", "psg": "paris saint germain",
        "manchester": "mancity", "man utd": "manchester united",
        "yankees": "new york yankees", "red sox": "boston red sox",
        "white sox": "chicago white sox", "phillies": "philadelphia phillies",
        "braves": "atlanta braves", "mets": "new york mets",
        "dodgers": "los angeles dodgers", "yankee": "new york yankee",
    }
    return alias.get(a) == b or alias.get(b) == a


def _encontrar_evento(data, home_name: str, away_name: str, event_date):
    """Evento cuyo PAR de competidores coincide con los equipos del pick."""
    comps = {c.get("id"): c for c in (data.get("competitors") or [])}
    h = _norm(home_name)
    a = _norm(away_name)
    if not h or not a:
        return None

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
        ok = (_coincide_equipo(n0, home_name) and _coincide_equipo(n1, away_name)) or (
            _coincide_equipo(n0, away_name) and _coincide_equipo(n1, home_name)
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
    """Aplana los ids de un mercado a la lista de odds reales.

    OJO: en GetEvents las cuotas vienen en 'oddIds', pero en GetEventDetails
    vienen en 'desktopOddIds'/'mobileOddIds' y 'oddIds' viene en None. Leer solo
    'oddIds' hacia que el mercado no tuviera ninguna cuota y el sistema
    pensara que el bookmaker no publica ese mercado.
    """
    salida = []
    vistos = set()
    grupos = []
    for clave in ("oddIds", "desktopOddIds", "mobileOddIds"):
        v = m.get(clave)
        if v:
            grupos.append(v)
    for grupo in grupos:
        if not isinstance(grupo, (list, tuple)):
            grupo = [grupo]
        for item in grupo:
            # cada item puede ser un id suelto o una lista de ids
            ids = item if isinstance(item, (list, tuple)) else [item]
            for oid in ids:
                if oid in vistos:
                    continue
                vistos.add(oid)
                o = odds_idx.get(oid)
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


def mercados_reales(sport: str, home_name: str, away_name: str, event_date=None,
                   min_odds: float = 1.20, max_odds: float = 2.50):
    """Mercados REALES del evento con su cuota publica, ya filtrados por rango.

    Devuelve una lista de dicts:
        {"market": <nombre del mercado>, "titulo": <texto para el usuario>,
         "selection": <lado>, "odds": <cuota real>, "linea": < handicap o None>}

    Es lo que permite REANALIZAR con datos de verdad en vez de inventar: la IA
    elige entre mercados que el sportsbook tiene publicados y con la cuota
    exacta, en vez de un catalogo teorico del que salia con cuotas estimadas
    (y por tanto commodities que luego nadie podia apostar).
    """
    data = get_events_deporte(sport)
    if not data:
        return []
    ev = _encontrar_evento(data, home_name, away_name, event_date)
    if not ev:
        return []
    odds_idx = _odds_index(data)
    comps = {c.get("id"): c for c in (data.get("competitors") or [])}
    cids = ev.get("competitorIds") or []
    nombres = [(comps.get(cid) or {}).get("name") or "" for cid in cids[:2]]
    if not nombres[0]:
        return []

    salida = []
    vistos = set()
    for m in _markets_de_evento(data, ev):
        nombre_mkt = (m.get("name") or "").strip()
        if not nombre_mkt:
            continue
        clave = nombre_mkt.lower()
        if clave in vistos:
            continue
        for o in _odds_de_market(m, odds_idx):
            p = _precio(o)
            if not p or not (min_odds <= p <= max_odds):
                continue
            sel = (o.get("name") or "").strip()
            linea = _linea_de(sel)
            # Formato 'Jugador (-2.5)' o 'Más de 7 (+7)': el numero de la
            # linea va en sv (a veces repetido dentro del nombre). Se separa el
            # numero del trailing para no repetirlo en el titulo.
            nombre_sel = re.sub(r"\s*\([+-]?\d+(?:[.,]\d+)?\)\s*$", "", sel).strip()
            sv = o.get("sv")
            if sv is None:
                sv = linea
            try:
                linea = float(sv) if sv is not None else None
            except (TypeError, ValueError):
                linea = None
            # Si la linea ya venia escrita en el nombre ('Más de 7'), no se
            # añade otra vez: solo se usa la linea cuando el nombre no la tiene.
            nombre_tiene_linea = bool(re.search(r"[+-]?\d+(?:[.,]\d+)?\s*$", nombre_sel))
            titulo = nombre_sel or (m.get("name") or "").strip()
            if linea is not None and nombre_sel and not nombre_tiene_linea:
                titulo = f"{nombre_sel} {linea:+g}"
            if not titulo:
                continue
            vistos.add(clave)
            salida.append({
                "market": nombre_mkt,
                "titulo": f"{titulo} ({nombre_mkt})",
                "selection": nombre_sel or sel,
                "odds": round(p, 3),
                "linea": linea,
            })
            break  # una linea por mercado basta para proponer

    salida.extend(_props_jugador(sport, data, ev, min_odds, max_odds))
    salida.extend(_mercados_detallados(sport, ev, min_odds, max_odds))
    return salida


def _mercados_detallados(sport, ev, min_odds, max_odds):
    """Mercados de GetEventDetails que NO estan en la lista basica de GetEvents.

    GetEvents solo trae 3-6 mercados por evento (Ganador, Hándicap, Totales),
    pero GetEventDetails trae cientos: 1x2 de cada mitad, doble oportunidad por
    tiempo, corners, tarjetas, props, hitos... El sistema solo miraba los
    primeros, asi que la IA recibia un catalogo de 3 mercados cuando el
    bookmaker tenia 340 publicables. Este bloque amplia la lista real.
    """
    detalle = _detalle_completo(ev.get("id"))
    if not detalle:
        return []
    odds_idx = {o.get("id"): o for o in (detalle.get("odds") or [])}
    salida = []
    vistos = set()
    for m in (detalle.get("markets") or []):
        nombre = (m.get("name") or "").strip()
        if not nombre:
            continue
        clave = nombre.lower()
        if clave in vistos:
            continue
        # DEDUPLICAR por (mercado, lado): el detalle trae el mismo mercado
        # varias veces (variantes desktop/mobile y duplicados de provider) y
        # sin esto la IA recibia la misma apuesta repetida dos veces.
        etiqueta = None
        precios = []
        for o in _odds_de_market(m, odds_idx):
            p = _precio(o)
            if p and min_odds <= p <= max_odds:
                precios.append(((o.get("name") or "").strip(), p))
        if not precios:
            continue
        etiqueta, precio = min(precios, key=lambda x: x[1])
        # Las COMBINADAS (dos mercados unidos por 'y' o '/') no se pueden
        # resolver de forma fiable: dependen de dos estadisticas a la vez y el
        # resolver solo mira una. Se omiten para no volver a crear el problema
        # de los anulados. Quedan los mercados simples, que son la mayoria.
        if re.search(r"\s+y\s|\sy\s+", nombre, re.I) or "/" in nombre:
            continue
        par = (clave, (etiqueta or "").lower())
        if par in vistos:
            continue
        vistos.add(par)
        # Se toma la linea con menor cuota: es la de mayor probabilidad y la
        # que el modelo elige cuando busca 'la mas facil de acertar'.
        salida.append({
            "market": nombre,
            "titulo": f"{etiqueta} ({nombre})" if etiqueta and etiqueta.lower() not in nombre.lower() else nombre,
            "selection": etiqueta or nombre,
            "odds": round(precio, 4),
            "linea": etiqueta or None,
        })
    return salida


def _props_jugador(sport, data, ev, min_odds, max_odds):
    """Props de JUGADOR del evento (hits, HR, strikeouts, bases).

    GetEvents solo trae los mercados principales; los props de jugador viven en
    GetEventDetails, dentro de 'childMarkets', y sus cuotas en 'desktopOddIds'.
    Antes no se leian, asi que el sistema no podia proponer un prop real aunque
    el bookmaker lo publicara (ej: 'Kyle Schwarber 1+ hit @ 1.59').
    """
    if sport not in ("mlb", "nba", "tennis"):
        return []
    detalle = _detalle_completo(ev.get("id"))
    if not detalle:
        return []
    odds_idx = {o.get("id"): o for o in (detalle.get("odds") or [])}
    salida = []
    vistos = set()
    for cm in detalle.get("childMarkets") or []:
        nombre = (cm.get("name") or "").strip()
        if not nombre or "por jugador" not in nombre.lower():
            continue
        # 'Hits totales por jugador (Kyle Schwarber (PHI)) (incl. extra innings)'
        m = re.search(r"^(.*?por jugador)\s*\(([^)]+)\)", nombre, re.I)
        if not m:
            continue
        mercado_base, jugador = m.group(1).strip(), m.group(2).strip()
        # 'Kyle Schwarber (PHI' -> 'Kyle Schwarber' (el regex anterior se
        # queda con el parentesis abierto porque el nombre lleva dos niveles
        # de parentesis: '(Jugador) (EQUIPO)'.
        jugador = re.sub(r"\s*\([A-Z]{2,4}\)?\s*$", "", jugador).strip()
        jugador = jugador.replace("(", "").replace(")", "").strip()
        if not jugador:
            continue
        # Tipo de prop legible: 'hits totales por jugador' -> 'hits'
        tipo = re.sub(r"\s*por\s*jugador.*$", "", mercado_base, flags=re.I).strip().lower()
        if not tipo:
            tipo = mercado_base.lower()
        clave = f"{mercado_base}|{jugador}".lower()
        if clave in vistos:
            continue
        for grupo in (cm.get("desktopOddIds") or cm.get("oddIds") or []):
            fila = []
            for oid in (grupo if isinstance(grupo, list) else [grupo]):
                o = odds_idx.get(oid) or {}
                p = _precio(o)
                if p and min_odds <= p <= max_odds:
                    fila.append(((o.get("name") or "").strip(), p))
            if not fila:
                continue
            # Se ofrecen hasta 3 lineas: las de mayor probabilidad (menor cuota)
            # son las que el modelo suele elegir y las que estan en rango publicable.
            for etiqueta, precio in sorted(fila, key=lambda x: x[1])[:3]:
                titulo = f"{jugador} {tipo} {etiqueta}"
                salida.append({
                    "market": mercado_base,
                    "titulo": titulo,
                    "selection": f"{jugador} {etiqueta}",
                    "odds": round(precio, 3),
                    "linea": etiqueta,
                    "jugador": jugador,
                    "prop": True,
                })
            vistos.add(clave)
            break
    return salida


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



def detalle_mercado_para_ia(event_id):
    """Lee TODOS los mercados, líneas, selecciones y cuotas del evento.

    No se trunca el payload: incluye mercados generales y childMarkets de
    props (jugadores, asistencias, remates, tarjetas, etc.). El límite de
    contexto del modelo se aplica después, si fuera necesario, de forma
    explícita y visible.
    """
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
    if not filas:
        return ""
    lineas = ["MERCADOS Y CUOTAS DORADOBET (incluye props de jugadores):"]
    for mercado, hijo, seleccion, precio, linea in filas:
        sufijo = f" linea={linea}" if linea and linea != "0.5" else ""
        lineas.append(f"- {mercado} | {hijo} | {seleccion}{sufijo} | cuota={precio:g}")
    return "\n".join(lineas)
