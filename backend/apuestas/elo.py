# -*- coding: utf-8 -*-
"""RATINGS ELO de equipos, calculados desde resultados reales de ESPN.

Por que existe
--------------
El generador de picks elegia en NBA/MLB/NFL/tenis con `_pick_deterministico`
(dashboard.py), que NO es un modelo: ordena los mercados por `peso_mercado` y,
a igualdad, por la cuota mas baja. Es decir, se apostaba a lo que ya estaba
barato, sin ninguna probabilidad calculada detras.

El modelo anterior de forma (`ai/analista_picks._fuerzas`) tampoco lo resuelve:
promedia puntos de los ultimos 5-10 partidos SIN mirar contra quien se jugo. Un
3-0 contra el colista pesa igual que contra el lider, asi que el multiplicador
se mueve por el calendario y no por el merito. Elo corrige exactamente eso.

Por que tabla propia
--------------------
`elo_ratings`, `elo_partidos` y `elo_calibracion` son tablas NUEVAS, creadas
con `create table if not exists` desde db.init_db(): eso sí se aplica en la BD
que ya esta en produccion. Añadir COLUMNAS a `ai_picks` habria exigido un ALTER
manual y habria roto el `ai_picks_archive like ai_picks` existente.

Escala
------
1500 = equipo medio, 400 puntos = 10x de probabilidad, ventaja de local en
`CONFIG`. La escala absoluta da igual: se usa la DIFERENCIA, y el paso
Elo -> puntos de marcador se ajusta por minimos cuadrados sobre partidos
historicos (`backend/apuestas/calibrar.py`).
"""

import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Ruta de ESPN por deporte. El futbol NO se puede listar aqui: ESPN da una ruta
# por LIGA (`soccer/eng.1`), y mezclar equipos de ligas distintas en un mismo
# pool de Elo no tiene sentido (un 1.8 de la Eredivisie no es un 1.8 de LaLiga).
# Por eso el futbol usa claves `soccer:<liga>` y se resuelven en `_ruta`.
RUTAS = {
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
    "wnba": "basketball/wnba",
}

# Parametros por deporte. `hfa` es ventaja de local EN PUNTOS ELO (no en puntos
# de marcador): el mapeo a marcador lo pone la calibracion con datos reales.
CONFIG = {
    "nba": {"k": 20.0, "hfa": 100.0, "mov": True, "rango_total": (120, 360)},
    "nfl": {"k": 20.0, "hfa": 55.0, "mov": True, "rango_total": (3, 130)},
    "mlb": {"k": 10.0, "hfa": 25.0, "mov": False, "rango_total": (1, 60)},
    "wnba": {"k": 20.0, "hfa": 90.0, "mov": True, "rango_total": (90, 300)},
    # Futbol: `mov` apagado a proposito. La formula de margen de victoria esta
    # pensada para deportes de anotacion alta (30 puntos de paliza); en futbol
    # casi todos los partidos son 0-3 goles de diferencia y aplicarla infla el
    # rating del que gana 4-0 contra un rival ya hundido.
    # `rango_total` admite 0-0: en futbol el 0-0 es un resultado, no un dato roto.
    "soccer": {"k": 20.0, "hfa": 65.0, "mov": False, "rango_total": (0, 14)},
}

INICIAL = 1500.0
# Frecuencia de prorroga de referencia, por si el endpoint de schedule no trae
# `linescores` y no se puede medir. En NBA ronda el 6.5%; en NFL es ~0.5%.
# En futbol no hay prorroga en el mercado de 90 minutos: 0.
P_OT_REFERENCIA = {"nba": 0.065, "wnba": 0.06, "nfl": 0.005, "mlb": 0.09,
                   "soccer": 0.0}
# Regresion entre temporadas: la plantilla cambia y el rating del año pasado no
# vale entero. 0.75 conserva la señal sin congelar el rating.
PESO_TEMPORADA = 0.75
# Ventana de la forma reciente (puntos a favor/en contra y total del partido).
VENTANA_FORMA = 15
TTL_SEGUNDOS = 1800

_cache = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# RED
# --------------------------------------------------------------------------
def _get(url: str, params: Optional[dict] = None, ttl: int = TTL_SEGUNDOS):
    clave = url + "?" + str(sorted((params or {}).items()))
    ahora = time.time()
    with _lock:
        hit = _cache.get(clave)
        if hit and ahora - hit[0] < ttl:
            return hit[1]
    datos = None
    try:
        r = requests.get(url, params=params, headers=UA, timeout=25)
        if r.status_code == 200:
            datos = r.json()
    except Exception as exc:  # noqa: BLE001 - red externa: se degrada, no rompe
        print(f"[elo] ESPN {url}: {exc}", flush=True)
    with _lock:
        _cache[clave] = (ahora, datos)
    return datos


def _ruta(sport: str) -> Optional[str]:
    """Ruta de ESPN. Acepta `nba` y `soccer:eng.1` (deporte:liga)."""
    s = (sport or "").strip().lower()
    if s in RUTAS:
        return RUTAS[s]
    if s.startswith("soccer:"):
        liga = s.split(":", 1)[1].strip()
        # ESPN exige la liga: sin ella no hay equipos ni calendario que pedir.
        return f"soccer/{liga}" if liga else None
    if s in ("soccer", "futbol", "football_soccer"):
        return "soccer/esp.1"
    return None


def _cfg(sport: str) -> Dict[str, Any]:
    """Configuracion del deporte. `soccer:eng.1` hereda la de `soccer`.

    Sin esta herencia, cada liga de futbol exigiria su propia entrada en CONFIG
    y una liga nueva (la que interese mañana) se quedaria sin parametros y sin
    poder calibrarse.
    """
    s = (sport or "").strip().lower()
    return CONFIG.get(s) or CONFIG.get(s.split(":", 1)[0]) or {}


def es_futbol(sport: str) -> bool:
    """True si el deporte se modela con goles (Poisson)."""
    s = (sport or "").strip().lower()
    return s == "soccer" or s.startswith("soccer:") or s in ("futbol", "football_soccer")


def equipos(sport: str) -> List[Dict[str, Any]]:
    """Equipos del deporte segun ESPN: [{id, name}]. Los ids son los de ESPN."""
    ruta = _ruta(sport)
    if not ruta:
        return []
    datos = _get(f"{ESPN_BASE}/{ruta}/teams")
    if not isinstance(datos, dict):
        return []
    salida = []
    for liga in (datos.get("sports") or [{}])[0].get("leagues") or []:
        for item in liga.get("teams") or []:
            t = item.get("team") or {}
            if t.get("id") and t.get("displayName"):
                salida.append({"id": str(t["id"]), "name": t["displayName"]})
    return salida


def _marcador(valor) -> Optional[float]:
    if isinstance(valor, dict):
        valor = valor.get("value")
    if isinstance(valor, list):
        valor = valor[0].get("value") if valor else None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def partidos_equipo(sport: str, team_id: str,
                    season: Optional[int] = None) -> List[Dict[str, Any]]:
    """Partidos TERMINADOS de un equipo, con marcador, local/visitante y si
    hubo prorroga.

    Una sola peticion por equipo y temporada (82 partidos en NBA). Se descartan
    los que aun no se han jugado: un rating solo se actualiza con resultados.
    """
    ruta = _ruta(sport)
    if not ruta or not team_id:
        return []
    params = {"season": int(season)} if season else None
    datos = _get(f"{ESPN_BASE}/{ruta}/teams/{team_id}/schedule", params)
    if not isinstance(datos, dict):
        return []

    salida = []
    for ev in datos.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        estado = ((comp.get("status") or {}).get("type") or {}).get("state", "")
        if estado != "post":
            continue
        fila = {"event_id": str(ev.get("id") or ""), "fecha": ev.get("date") or ""}
        prorroga = False
        for c in comp.get("competitors") or []:
            lado = c.get("homeAway")
            if lado not in ("home", "away"):
                continue
            equipo = c.get("team") or {}
            fila[lado] = {
                "id": str(equipo.get("id") or ""),
                "nombre": equipo.get("displayName") or "",
                "score": _marcador(c.get("score")),
            }
            # Mas de 4 periodos = prorroga. Es la unica forma fiable de medirla
            # sin play-by-play, y hace falta porque los mercados de Doradobet
            # dicen literalmente "incl. prorroga".
            if len(c.get("linescores") or []) > 4:
                prorroga = True
        if "home" not in fila or "away" not in fila:
            continue
        if fila["home"]["score"] is None or fila["away"]["score"] is None:
            continue
        if not fila["home"]["id"] or not fila["away"]["id"]:
            continue
        # SANIDAD DEL DATO. Un partido con marcador imposible (0-0 o 300-250)
        # envenena la desviacion tipica: con 2641 partidos, una docena de filas
        # corruptas suben sigma_total de 18 a 23 y el motor empieza a ver
        # "edges" en todos los totales. Se descartan antes de calibrar.
        rango = _cfg(sport).get("rango_total")
        if rango:
            total_partido = fila["home"]["score"] + fila["away"]["score"]
            if not (rango[0] <= total_partido <= rango[1]):
                continue
        fila["prorroga"] = prorroga
        salida.append(fila)
    return salida


def _temporadas(cuantas: int = 3, sport: str = "nba") -> List[Optional[int]]:
    """Temporadas a descargar. `None` = la actual (sin parametro).

    OJO: las dos convenciones de ESPN no coinciden.
      * NBA/NFL: la temporada se etiqueta por el año en que TERMINA (en
        noviembre de 2026 la temporada en curso es la 2027). `season=2026` es la
        anterior.
      * FUTBOL: se etiqueta por el año en que EMPIEZA (en noviembre de 2026 la
        temporada en curso es la 2026-27, o sea `season=2026`). Si se usara la
        regla de la NBA se pedia [actual, 2026, 2025], donde 2026 ES la actual:
        se descargaba la misma temporada dos veces y se perdia una entera.
    """
    anio = datetime.now(timezone.utc).year
    lista: List[Optional[int]] = [None]
    # En futbol el año del parametro es el de INICIO: la temporada anterior es
    # anio-1, no anio.
    base = anio - 1 if es_futbol(sport) else anio
    for i in range(max(0, cuantas - 1)):
        lista.append(base - i)
    return lista


def _mov(margen: float, elo_ganador: float, elo_perdedor: float) -> float:
    """Multiplicador por margen de victoria (formula de FiveThirtyEight).

    Sin esto una paliza de 30 puntos mueve el rating igual que un +30 robado y
    el rating se infla. Dividiendo por la diferencia de Elo, la paliza esperada
    mueve poco y la sorpresa mueve mucho.
    """
    dif = elo_ganador - elo_perdedor
    return ((margen + 3.0) ** 0.8) / (7.5 + 0.006 * dif)


# --------------------------------------------------------------------------
# CONSTRUCCION DEL RATING
# --------------------------------------------------------------------------
def construir(sport: str, cuantas_temporadas: int = 3) -> Dict[str, Any]:
    """Procesa N temporadas de golpe y devuelve ratings + muestras para calibrar.

    Devuelve:
      ratings  -> {equipo_id: {rating, partidos, fecha_ultimo}}
      muestras -> cada partido con las features que habia ENTONCES (elo de los
                  dos, descanso, forma reciente) y su resultado. Se guardan
                  ANTES de actualizar el rating, para que la calibracion no vea
                  el futuro del propio partido (leakage).
      liga     -> medias de la liga.
      forma    -> forma reciente actual de cada equipo (para predecir).

    Se procesa en orden cronologico porque Elo es secuencial: el rating de un
    partido depende solo de los anteriores.
    """
    cfg = _cfg(sport)
    vacio = {"ratings": {}, "muestras": [], "liga": {}, "partidos": 0, "forma": {}}
    if not cfg:
        return vacio

    ids = [e["id"] for e in equipos(sport)]
    if not ids:
        return vacio

    # Una peticion por equipo y temporada, deduplicando por event_id.
    unicos: Dict[str, Dict[str, Any]] = {}
    for season in _temporadas(cuantas_temporadas, sport):
        for tid in ids:
            for p in partidos_equipo(sport, tid, season):
                if p["event_id"] and p["event_id"] not in unicos:
                    unicos[p["event_id"]] = p

    partidos = sorted(unicos.values(), key=lambda x: x.get("fecha") or "")
    if not partidos:
        return vacio

    ratings: Dict[str, float] = {}
    jugados: Dict[str, int] = {}
    ultimo: Dict[str, str] = {}
    # Forma reciente por equipo: puntos a favor, en contra y total de sus
    # ultimos VENTANA_FORMA partidos. Es la pata "ritmo" del modelo de puntos:
    # Elo dice quien es mejor, esto dice cuanto se anota en sus partidos.
    forma: Dict[str, List[Dict[str, float]]] = {}
    muestras: List[Dict[str, Any]] = []
    totales_liga: List[float] = []
    prorrogas = 0
    temporada_actual = None

    def _resumen(tid):
        filas = forma.get(tid) or []
        if not filas:
            return None
        n = len(filas)
        return {
            "pf": round(sum(f["pf"] for f in filas) / n, 2),
            "pa": round(sum(f["pa"] for f in filas) / n, 2),
            "total": round(sum(f["total"] for f in filas) / n, 2),
            "n": n,
        }

    def _descanso(tid, fecha):
        prev = ultimo.get(tid)
        if not prev:
            return None
        try:
            a = datetime.fromisoformat(prev.replace("Z", "+00:00"))
            b = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
            return max(0, min(30, (b - a).days))
        except (ValueError, TypeError):
            return None

    for p in partidos:
        fecha = p.get("fecha") or ""
        h, a = p["home"], p["away"]
        score_h, score_a = h["score"], a["score"]

        # Regresion entre temporadas: al cambiar de año se encoge el rating
        # hacia 1500 en vez de arrastrarlo entero a la temporada siguiente.
        temporada = fecha[:4]
        if temporada_actual is None:
            temporada_actual = temporada
        elif temporada != temporada_actual:
            for tid in list(ratings):
                ratings[tid] = INICIAL + PESO_TEMPORADA * (ratings[tid] - INICIAL)
            temporada_actual = temporada

        elo_h = ratings.get(h["id"], INICIAL)
        elo_a = ratings.get(a["id"], INICIAL)
        res_h, res_a = _resumen(h["id"]), _resumen(a["id"])

        muestras.append({
            "event_id": p["event_id"],
            "fecha": fecha,
            "home_id": h["id"],
            "away_id": a["id"],
            "elo_h": elo_h,
            "elo_a": elo_a,
            "descanso_h": _descanso(h["id"], fecha),
            "descanso_a": _descanso(a["id"], fecha),
            "total_h": res_h["total"] if res_h else None,
            "total_a": res_a["total"] if res_a else None,
            "margen": score_h - score_a,
            "total": score_h + score_a,
            "prorroga": bool(p.get("prorroga")),
        })
        totales_liga.append(score_h + score_a)
        if p.get("prorroga"):
            prorrogas += 1

        # --- Actualizacion Elo (con la muestra ya guardada) ---
        dif = (elo_h + cfg["hfa"]) - elo_a
        esperado_h = 1.0 / (1.0 + 10 ** (-dif / 400.0))
        gano_h = score_h > score_a
        resultado_h = 1.0 if gano_h else (0.5 if score_h == score_a else 0.0)
        margen = abs(score_h - score_a)
        if cfg["mov"] and margen > 0:
            ganador, perdedor = (elo_h, elo_a) if gano_h else (elo_a, elo_h)
            mov = _mov(margen, ganador, perdedor)
        else:
            mov = 1.0
        delta = cfg["k"] * mov * (resultado_h - esperado_h)
        ratings[h["id"]] = elo_h + delta
        ratings[a["id"]] = elo_a - delta
        jugados[h["id"]] = jugados.get(h["id"], 0) + 1
        jugados[a["id"]] = jugados.get(a["id"], 0) + 1
        ultimo[h["id"]] = fecha
        ultimo[a["id"]] = fecha

        for tid, pf, pa in ((h["id"], score_h, score_a),
                            (a["id"], score_a, score_h)):
            fila = forma.setdefault(tid, [])
            fila.append({"pf": pf, "pa": pa, "total": pf + pa})
            if len(fila) > VENTANA_FORMA:
                fila.pop(0)

    n = len(totales_liga)
    liga = {
        "total_medio": round(sum(totales_liga) / n, 3) if n else 0.0,
        "prorrogas_pct": round(prorrogas / n, 4) if n else 0.0,
        "partidos": n,
        "equipos": len(ids),
    }
    return {
        "ratings": {tid: {"rating": round(r, 2),
                          "partidos": jugados.get(tid, 0),
                          "fecha_ultimo": ultimo.get(tid)}
                    for tid, r in ratings.items()},
        "muestras": muestras,
        "liga": liga,
        "partidos": n,
        "forma": {tid: _resumen(tid) for tid in ratings},
    }


# --------------------------------------------------------------------------
# AJUSTE: de Elo a puntos de marcador
# --------------------------------------------------------------------------
def _resolver(matriz: List[List[float]], vector: List[float]) -> Optional[List[float]]:
    """Gauss-Jordan con pivoteo. Matriz pequeña (2x2 / 3x3), sin numpy.

    `requirements.txt` no trae numpy ni scipy, y añadirlos engorda la imagen de
    Docker solo para resolver un sistema de 3 incognitas.
    """
    n = len(vector)
    m = [list(map(float, fila)) + [float(vector[i])] for i, fila in enumerate(matriz)]
    for col in range(n):
        pivote = max(range(col, n), key=lambda f: abs(m[f][col]))
        if abs(m[pivote][col]) < 1e-12:
            return None
        m[col], m[pivote] = m[pivote], m[col]
        div = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= div
        for f in range(n):
            if f == col:
                continue
            factor = m[f][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                m[f][j] -= factor * m[col][j]
    return [m[i][n] for i in range(n)]


def _minimos_cuadrados(filas: List[List[float]],
                       y: List[float]) -> Optional[List[float]]:
    """Coeficientes por minimos cuadrados: (X'X)^-1 X'y, sin numpy."""
    if not filas or len(filas) < len(filas[0]) + 2:
        return None
    k = len(filas[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for fila, objetivo in zip(filas, y):
        for i in range(k):
            xty[i] += fila[i] * objetivo
            for j in range(k):
                xtx[i][j] += fila[i] * fila[j]
    return _resolver(xtx, xty)


def _desviacion(valores: List[float], media: float) -> float:
    if len(valores) < 2:
        return 0.0
    return math.sqrt(sum((v - media) ** 2 for v in valores) / (len(valores) - 1))


# Ventana para anclar el NIVEL de los totales a la liga de hoy.
DIAS_RECIENTES = 21


def _corte_reciente(muestras: List[Dict[str, Any]],
                    dias: int = DIAS_RECIENTES) -> Optional[str]:
    """Fecha de corte (YYYY-MM-DD) de los ultimos `dias` dias con partidos."""
    fechas = sorted(m.get("fecha") or "" for m in muestras if m.get("fecha"))
    if not fechas:
        return None
    try:
        ultima = datetime.fromisoformat(fechas[-1].replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (ultima - timedelta(days=dias)).strftime("%Y-%m-%d")


def ajustar(muestras: List[Dict[str, Any]], liga: Dict[str, Any],
            sport: str) -> Dict[str, Any]:
    """Ajusta Elo -> (margen esperado, total esperado) y MIDE la incertidumbre.

    Dos regresiones separadas, porque son dos cosas distintas:
        margen = a + b*(elo_local + HFA - elo_visita)/100 + c*(descanso_local - descanso_visita)
        total  = t0 + t1*(ritmo_medio_de_los_dos_equipos)

    Las desviaciones y la correlacion se MIDEN de los residuos. El modelo de NBA
    anterior tenia SD_MARGEN=13.5 y SD_EQUIPO=12.5 escritos a mano: si la liga
    cambia de ritmo, esos numeros mienten y nadie se entera.
    """
    cfg = _cfg(sport) or {}
    hfa = cfg.get("hfa", 0.0)
    valida = [m for m in muestras if m.get("elo_h") is not None]

    # ---- Margen ----
    filas, objetivos = [], []
    for m in valida:
        rest_h = m["descanso_h"] if m["descanso_h"] is not None else 4.0
        rest_a = m["descanso_a"] if m["descanso_a"] is not None else 4.0
        filas.append([1.0, (m["elo_h"] + hfa - m["elo_a"]) / 100.0, rest_h - rest_a])
        objetivos.append(m["margen"])
    coef = _minimos_cuadrados(filas, objetivos)
    sin_descanso = False
    if not coef:
        # La columna de descanso puede salir CONSTANTE (partidos sin fecha, o
        # todos con los mismos dias). Entonces X'X es singular y el ajuste
        # devolveria ceros: el modelo predeciria margen 0 para TODOS los
        # partidos y nadie se enteraria porque el numero sigue siendo valido.
        # Se reajusta sin la columna en vez de degradar en silencio.
        coef2 = _minimos_cuadrados([f[:2] for f in filas], objetivos)
        if coef2:
            coef = [coef2[0], coef2[1], 0.0]
            sin_descanso = True
        else:
            coef = [0.0, 0.0, 0.0]
    residuos = [y - sum(c * f for c, f in zip(coef, fila))
                for fila, y in zip(filas, objetivos)]
    media_res = sum(residuos) / len(residuos) if residuos else 0.0
    sigma_margen = _desviacion(residuos, media_res)

    # ---- Total ----
    con_ritmo = [m for m in valida
                 if m.get("total_h") is not None and m.get("total_a") is not None]
    coef_total = [float(liga.get("total_medio") or 0.0), 0.0]
    residuos_total: List[float] = []
    total_reciente = None
    if len(con_ritmo) > 30:
        filas_t = [[1.0, (m["total_h"] + m["total_a"]) / 2.0] for m in con_ritmo]
        objetivos_t = [m["total"] for m in con_ritmo]
        ajuste = _minimos_cuadrados(filas_t, objetivos_t)
        if ajuste:
            coef_total = ajuste

        # CORRECCION DE NIVEL con la ventana reciente. Los totales de la liga
        # suben y bajan por temporada (ritmo de juego, reglas de faltas). Si el
        # intercepto se queda anclado a la media de 3 temporadas mientras el
        # mercado cotiza la liga de HOY, el modelo cree que TODOS los totales
        # estan infravalorados y publica "edges" del 30% que son puro sesgo.
        # Medido: 229.8 de total esperado contra lineas reales de 218.5.
        corte = _corte_reciente(con_ritmo, dias=DIAS_RECIENTES)
        recientes = [(fila, obj) for fila, obj, m in
                     zip(filas_t, objetivos_t, con_ritmo)
                     if corte and (m.get("fecha") or "") >= corte]
        if len(recientes) >= 20:
            desvio = sum(obj - sum(c * f for c, f in zip(coef_total, fila))
                         for fila, obj in recientes) / len(recientes)
            coef_total = [coef_total[0] + desvio, coef_total[1]]
            total_reciente = round(
                sum(obj for _, obj in recientes) / len(recientes), 3)

        residuos_total = [y - sum(c * f for c, f in zip(coef_total, fila))
                          for fila, y in zip(filas_t, objetivos_t)]
    if len(residuos_total) > 2:
        media_t = sum(residuos_total) / len(residuos_total)
        sigma_total = _desviacion(residuos_total, media_t)
    else:
        # Sin historia suficiente: se usa la relacion tipica de NBA/NFL
        # (el total varia mas que el margen).
        sigma_total = max(sigma_margen, 1.0) * 1.25
        media_t = 0.0

    # Correlacion entre los residuos de margen y total (ritmo compartido).
    rho = 0.0
    if len(residuos) == len(residuos_total) and len(residuos) > 30:
        cov = sum((a - media_res) * (b - media_t) for a, b in zip(residuos, residuos_total)) \
            / len(residuos)
        sm, st = sigma_margen or 1.0, sigma_total or 1.0
        rho = max(-0.9, min(0.9, cov / (sm * st)))

    # De (margen, total) a los dos marcadores por separado. Identidades exactas
    # para dos variables simetricas:
    #     Var(margen) = 2*s2*(1-r)      Var(total) = 2*s2*(1+r)
    #     => s2 = (Vm + Vt)/4           r = (Vt - Vm)/(Vt + Vm)
    # Es lo que permite simular los DOS equipos (y asi tener totales por equipo)
    # sin inventar una correlacion a ojo.
    vm, vt = sigma_margen ** 2, sigma_total ** 2
    sigma_equipo = math.sqrt(max((vm + vt) / 4.0, 1.0))
    rho_equipos = (vt - vm) / (vt + vm) if (vt + vm) > 0 else 0.0
    rho_equipos = max(-0.9, min(0.9, rho_equipos))

    # Diagnosticos "en bruto": sirven para comprobar que sigma no se ha inflado
    # por un fallo del ajuste. Si sigma_total se parece a la desviacion BRUTA de
    # los totales, la feature de ritmo no aporta y hay que revisarla.
    margenes = [m["margen"] for m in valida]
    totales = [m["total"] for m in valida]
    media_margen = sum(margenes) / len(margenes) if margenes else 0.0
    media_total_obs = sum(totales) / len(totales) if totales else 0.0

    # Prorroga: si el endpoint de schedule no trae `linescores`, la medicion sale
    # 0 y se usa la referencia real de la liga (~6.5% en NBA). Dejar 0 haria que
    # el Monte Carlo ignorase la prorroga mientras la cuota del bookmaker SI la
    # incluye: eso sesga sistematicamente los mercados de totales hacia el UNDER.
    p_ot = float(liga.get("prorrogas_pct") or 0.0)
    if p_ot <= 0:
        p_ot = P_OT_REFERENCIA.get(sport, 0.0)

    return {
        "sport": sport,
        "a": round(coef[0], 4),
        "b": round(coef[1], 4),
        "c": round(coef[2], 4),
        "t0": round(coef_total[0], 4),
        "t1": round(coef_total[1], 4),
        "sigma_margen": round(sigma_margen, 4),
        "sigma_total": round(sigma_total, 4),
        "sigma_equipo": round(sigma_equipo, 4),
        "rho": round(rho, 4),
        "rho_equipos": round(rho_equipos, 4),
        "p_ot": round(p_ot, 4),
        "total_medio": round(float(liga.get("total_medio") or 0.0), 3),
        "n_partidos": len(valida),
        "sd_margen_bruta": round(_desviacion(margenes, media_margen), 4),
        "sd_total_bruta": round(_desviacion(totales, media_total_obs), 4),
        "ventaja_local": round(media_margen, 3),
        "hfa_efectivo": round(coef[0] + coef[1] * hfa / 100.0, 3),
        "sin_descanso": sin_descanso,
        "total_reciente": total_reciente,
    }


# --------------------------------------------------------------------------
# PERSISTENCIA (degrada a memoria si la BD no esta disponible)
# --------------------------------------------------------------------------
_memoria: Dict[str, Dict[str, Any]] = {}


def _db():
    try:
        import db as _db_mod
        return _db_mod
    except Exception:  # noqa: BLE001 - sin BD el modulo sigue sirviendo en memoria
        return None


def _fecha_mysql(valor) -> Optional[str]:
    """'2026-11-12T00:00Z' (formato ESPN) -> '2026-11-12 00:00:00' (MySQL).

    Sin esto el INSERT falla con "Incorrect datetime value": ESPN usa la T y la
    Z de la ISO-8601 y MySQL espera un espacio y sin zona.
    """
    texto = str(valor or "").strip().replace("T", " ").replace("Z", "")
    if not texto:
        return None
    if len(texto) == 16:  # 'YYYY-MM-DD HH:MM' -> falta el segundero
        texto += ":00"
    return texto[:19]



def guardar(sport: str, ratings: Dict[str, Any], calibracion: Dict[str, Any]) -> bool:
    """Guarda ratings y calibracion. Devuelve False si no se pudo persistir.

    Los ratings se guardan CON su forma reciente (pf/pa/total) porque la
    prediccion de puntos necesita las dos cosas juntas: Elo dice quien es mejor
    y la forma dice cuanto se anota en sus partidos. Si solo se guardara el
    rating, cada ciclo tendria que volver a descargar la temporada entera.
    """
    _memoria[sport] = {"ratings": ratings, "calibracion": calibracion,
                       "ts": time.time()}
    base = _db()
    if not base:
        return False
    try:
        # Fail-fast: si la tabla no existe (o la BD no responde), se aborta AQUI.
        # Sin esto el bucle intentaba 30 inserts, cada uno con 5 reintentos y 3 s
        # de espera = varios minutos perdidos por un problema que ya se sabia.
        if not base.run_query("delete from elo_ratings where sport = %s", (sport,)):
            print("[elo] la tabla elo_ratings no responde (¿falta crearla? "
                  "ejecuta crear_tablas_elo.py); se sigue solo en memoria",
                  flush=True)
            return False
        # La calibracion tambien se REEMPLAZA, no se acumula. Antes cada
        # calibracion insertaba una fila nueva: la tabla acabo con 7 filas de
        # 'nba' (sigma_total 23.27 al lado de 18.91). `cargar()` lee la ultima,
        # pero cualquier consulta sin ORDER BY veia una calibracion vieja y el
        # motor parecia roto.
        base.run_query("delete from elo_calibracion where sport = %s", (sport,))
        for tid, datos in ratings.items():
            base.run_query(
                "insert into elo_ratings (sport, entidad_id, rating, partidos,"
                " fecha_ultimo, pf, pa, total, n_forma, actualizado)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (sport, str(tid), datos.get("rating"), datos.get("partidos") or 0,
                 _fecha_mysql(datos.get("fecha_ultimo")),
                 datos.get("pf"), datos.get("pa"), datos.get("total"),
                 datos.get("n") or 0, base.now_utc()),
            )
        base.run_query(
            "insert into elo_calibracion (sport, a, b, c, t0, t1, sigma_margen,"
            " sigma_total, sigma_equipo, rho, rho_equipos, p_ot, total_medio,"
            " n_partidos, creado) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sport, calibracion.get("a"), calibracion.get("b"), calibracion.get("c"),
             calibracion.get("t0"), calibracion.get("t1"),
             calibracion.get("sigma_margen"), calibracion.get("sigma_total"),
             calibracion.get("sigma_equipo"), calibracion.get("rho"),
             calibracion.get("rho_equipos"), calibracion.get("p_ot"),
             calibracion.get("total_medio"), calibracion.get("n_partidos"),
             base.now_utc()),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[elo] no se pudo guardar en BD: {exc}", flush=True)
        return False
    return True


CLAVES_CALIBRACION = ("a", "b", "c", "t0", "t1", "sigma_margen", "sigma_total",
                      "sigma_equipo", "rho", "rho_equipos", "p_ot",
                      "total_medio", "n_partidos")


def cargar(sport: str, max_horas: float = 12.0) -> Optional[Dict[str, Any]]:
    """Ratings + calibracion guardados, si son recientes. None si no hay."""
    memo = _memoria.get(sport)
    if memo and (time.time() - memo["ts"]) / 3600.0 < max_horas:
        return {"ratings": memo["ratings"], "calibracion": memo["calibracion"],
                "origen": "memoria"}
    base = _db()
    if not base:
        return None
    try:
        filas = base.run_query(
            "select entidad_id, rating, partidos, pf, pa, total, n_forma, fecha_ultimo"
            " from elo_ratings where sport = %s", (sport,),
        )
    except Exception:  # noqa: BLE001
        return None
    if not filas:
        return None
    ratings = {}
    for fila in filas:
        if not fila:
            continue
        # OJO: db.run_query usa DictCursor, las filas son DICCIONARIOS. Acceder
        # por indice (fila[1]) lanza KeyError y rompe todo el motor.
        try:
            tid = fila.get("entidad_id")
            if tid is None:
                continue
            ratings[str(tid)] = {
                "rating": float(fila.get("rating") or INICIAL),
                "partidos": int(fila.get("partidos") or 0),
                "pf": float(fila["pf"]) if fila.get("pf") is not None else None,
                "pa": float(fila["pa"]) if fila.get("pa") is not None else None,
                "total": float(fila["total"]) if fila.get("total") is not None else None,
                "n": int(fila.get("n_forma") or 0),
                "fecha_ultimo": (str(fila["fecha_ultimo"])
                                 if fila.get("fecha_ultimo") else None),
            }
        except (TypeError, ValueError, KeyError):
            continue
    calibracion = {}
    try:
        fila = base.run_query(
            "select " + ", ".join(CLAVES_CALIBRACION) + " from elo_calibracion"
            " where sport = %s order by id desc limit 1", (sport,), fetchone=True,
        )
        if fila:
            calibracion = {k: (float(fila[k]) if fila.get(k) is not None else 0.0)
                           for k in CLAVES_CALIBRACION}
            calibracion["sport"] = sport
    except Exception:  # noqa: BLE001
        calibracion = {}
    if not calibracion:
        return None
    _memoria[sport] = {"ratings": ratings, "calibracion": calibracion,
                       "ts": time.time()}
    return {"ratings": ratings, "calibracion": calibracion, "origen": "bd"}


def actualizar(sport: str, cuantas_temporadas: int = 3) -> Dict[str, Any]:
    """Recalcula desde cero y guarda. Punto de entrada del cron o del script."""
    datos = construir(sport, cuantas_temporadas)
    calibracion = ajustar(datos.get("muestras") or [], datos.get("liga") or {}, sport)
    # La forma reciente se fusiona con el rating para que se guarde en la misma
    # fila y el ciclo del dashboard no tenga que recalcular nada.
    ratings = datos.get("ratings") or {}
    for tid, resumen in (datos.get("forma") or {}).items():
        if resumen and tid in ratings:
            ratings[tid].update(resumen)
    guardar(sport, ratings, calibracion)
    return {"ratings": ratings, "calibracion": calibracion,
            "liga": datos.get("liga") or {}, "partidos": datos.get("partidos") or 0}


def estado(sport: str, forzar: bool = False) -> Dict[str, Any]:
    """Ratings listos para usar: de cache/BD si son recientes, si no recalcula."""
    if not forzar:
        guardado = cargar(sport)
        if guardado:
            return guardado
    hecho = actualizar(sport)
    return {"ratings": hecho["ratings"], "calibracion": hecho["calibracion"],
            "origen": "recalculado"}


def nombre_equipo(sport: str, team_id: str) -> str:
    for e in equipos(sport):
        if e["id"] == str(team_id):
            return e["name"]
    return ""


__all__ = [
    "CONFIG", "INICIAL", "actualizar", "ajustar", "cargar", "construir",
    "equipos", "estado", "guardar", "nombre_equipo", "partidos_equipo",
]






