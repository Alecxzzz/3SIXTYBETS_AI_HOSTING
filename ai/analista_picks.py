"""
ANALISTA DE PICKS DEL DASHBOARD - motor INDEPENDIENTE del chat.

Por que existe un modulo aparte
-------------------------------
El chat y el dashboard son superficies distintas y deben tener motores
distintos. Si compartieran proveedor, una oleada de trafico en el chat dejaria
al dashboard mudo (y al reves): los proveedores aplican el limite POR CUENTA y
POR MODELO, asi que la cuota que gasta el chat es cuota que no puede gastar el
dashboard.

Este modulo resuelve el analisis en DOS CAPAS:

  CAPA 1 - CUANTITATIVA (siempre funciona, NO necesita API key)
      Modelo Poisson tipo Dixon-Coles sobre la forma REAL de ambos equipos,
      sacada de ESPN (data_engine). Calcula la probabilidad justa del pick, la
      compara con la cuota de la casa y saca el EDGE y el EV.

  CAPA 2 - LLM (opcional)
      Redacta el informe en lenguaje natural. Usa un PROVEEDOR PROPIO
      (variables ANALISTA_AI_*) para no competir por la cuota de Groq con el
      chat. Si no hay key, el analisis cuantitativo se devuelve igual: nunca se
      queda sin analisis.

Configuracion (todas opcionales salvo la key para la capa 2):
  ANALISTA_AI_API_KEY    key del proveedor del analista (propia, NO la del chat)
  ANALISTA_AI_BASE_URL   endpoint chat completions formato OpenAI
  ANALISTA_AI_MODEL      modelo principal
  ANALISTA_AI_FALLBACK   modelo de respaldo
  ANALISTA_AI_TIMEOUT    segundos (por defecto 45)
  ANALISTA_AI_MAX_TOKENS tokens de salida (por defecto 900)

Proveedores gratuitos verificados (todos OpenAI-compatible, sin tarjeta):
  OpenRouter  https://openrouter.ai/api/v1            (modelos :free)
  Google AI   https://generativelanguage.googleapis.com/v1beta/openai/
  Mistral     https://api.mistral.ai/v1
  Cerebras    https://api.cerebras.ai/v1
"""

import math
import os
import time
from typing import Any, Dict, List, Optional

import requests

from ai.claves import claves, claves_openrouter
from ai.proveedor_fallos import sin_cuota

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv es opcional
    load_dotenv = None

if load_dotenv:
    load_dotenv()


# --------------------------------------------------------------------------
# PROVEEDOR PROPIO (aislado del chat y del generador de picks)
# --------------------------------------------------------------------------
API_KEY = os.getenv("ANALISTA_AI_API_KEY") or ""
BASE_URL = os.getenv(
    "ANALISTA_AI_BASE_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)
MODELO = os.getenv(
    "ANALISTA_AI_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
)
MODELO_FALLBACK = os.getenv(
    "ANALISTA_AI_FALLBACK", "nvidia/nemotron-3.5-lightning:free"
)
# Los modelos de razonamiento gastan max_tokens ANTES de escribir el informe:
# con 900 el content llegaba vacio. 2500 deja margen de sobra.
MAX_TOKENS = int(os.getenv("ANALISTA_AI_MAX_TOKENS", "2500"))
TIMEOUT = int(os.getenv("ANALISTA_AI_TIMEOUT", "60"))
REASONING = os.getenv("ANALISTA_AI_REASONING", "low")

# Rotacion de cuentas (mismo motivo que el dashboard): el cupo :free de
# OpenRouter es DIARIO POR CUENTA; si la principal se agota, la clave de
# respaldo mantiene el analisis vivo sin tocar configuracion.
KEYS = (
    claves_openrouter(API_KEY, os.getenv("ANALISTA_AI_API_KEY_FALLBACK"))
    if "openrouter.ai" in BASE_URL
    else claves(API_KEY)
)

# Cuentas que respondieron 'cuota agotada' hace poco: se saltan un rato para
# no pagar un 429 de ida y vuelta en cada analisis.
_CLAVES_EN_PAUSA = {}
_PAUSA_CLAVE_S = int(os.getenv("ANALISTA_AI_PAUSA_KEY", "1800"))

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_SEARCH = "https://site.api.espn.com/apis/common/v3/search"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Media de goles POR EQUIPO Y PARTIDO en las ligas que cubrimos. Se usa como
# referencia para separar "ataque fuerte" de "defensa floja": sin una base
# comun, 2 goles de media parecerian mucho en una liga cerrada y poco en una
# abierta. Valores tipicos de futbol profesional (total 2.5-2.8 por partido).
MEDIA_GOLES_EQUIPO = 1.35
VENTAJA_LOCAL = 1.12
MAX_GOLES = 10


# --------------------------------------------------------------------------
# UTILIDADES
# --------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    """ESPN devuelve el marcador como {'value': 4.0} o como 4 segun endpoint."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _espn_path(sport: str, league: Optional[str]) -> str:
    """Ruta ESPN del deporte. 'usa.1' -> 'soccer/usa.1'; 'football/nfl' tal cual."""
    lg = (league or "").strip()
    if lg and "/" in lg:
        return lg
    s = (sport or "").lower()
    if s in ("soccer", "futbol", "football_soccer"):
        return "soccer/" + (lg or "esp.1")
    if s in ("nfl", "americanfootball"):
        return "football/nfl"
    if s == "nba":
        return "basketball/nba"
    if s == "tenis" or s == "tennis":
        return "tennis/atp"
    return lg or s


def _es_soccer(sport: str, league: Optional[str]) -> bool:
    """True si el deporte se puede modelar con Poisson de goles."""
    p = _espn_path(sport, league).lower()
    return p.startswith("soccer/")


def equipo_id(nombre: str) -> Optional[Dict[str, Any]]:
    """Busca el id de un equipo en ESPN. {id, name, league}."""
    if not nombre:
        return None
    try:
        r = requests.get(
            ESPN_SEARCH,
            params={"query": nombre, "limit": 10},
            headers=UA,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        for item in r.json().get("items", []):
            if item.get("type") == "team":
                return {
                    "id": item.get("id"),
                    "name": item.get("displayName"),
                    "league": item.get("defaultLeagueSlug"),
                }
    except Exception as e:  # noqa: BLE001 - red externa, no debe tumbar el analisis
        print(f"[analista equipo_id] {e}", flush=True)
    return None


def forma_equipo(sport: str, league: Optional[str], equipo: str,
                 n: int = 6) -> List[Dict[str, Any]]:
    """Ultimos n partidos REALES de un equipo segun ESPN.

    Devuelve [{gf, ga, rival, local, fecha}] del mas reciente al mas antiguo.
    Una sola peticion por equipo (schedule), no hay que escanear el calendario.
    """
    info = equipo_id(equipo)
    if not info or not info.get("id"):
        return []
    lg = league or info.get("league")
    url = f"{ESPN_BASE}/{_espn_path(sport, lg)}/teams/{info['id']}/schedule"
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200:
            return []
        eventos = r.json().get("events", []) or []
    except Exception as e:  # noqa: BLE001
        print(f"[analista forma_equipo] {e}", flush=True)
        return []

    salida = []
    for ev in eventos:
        comps = (ev.get("competitions") or [{}])[0]
        competidores = comps.get("competitors") or []
        if len(competidores) < 2:
            continue
        propio = None
        rival = None
        for c in competidores:
            tid = str((c.get("team") or {}).get("id") or "")
            if tid == str(info["id"]):
                propio = c
            else:
                rival = c
        if not propio or not rival:
            continue
        gf = _num(propio.get("score"))
        ga = _num(rival.get("score"))
        if gf is None or ga is None:
            continue  # partido aun no jugado
        salida.append({
            "gf": gf,
            "ga": ga,
            "rival": (rival.get("team") or {}).get("displayName"),
            "local": (propio.get("homeAway") == "home"),
            "fecha": (ev.get("date") or "")[:10],
        })

    salida.sort(key=lambda x: x["fecha"], reverse=True)
    return salida[:n]


def _promedios(forma: List[Dict[str, Any]]) -> Dict[str, float]:
    """Promedios de goles a favor/en contra y tamano de la muestra."""
    if not forma:
        return {"gf": 0.0, "ga": 0.0, "n": 0}
    n = len(forma)
    return {
        "gf": sum(p["gf"] for p in forma) / n,
        "ga": sum(p["ga"] for p in forma) / n,
        "n": n,
    }


def _media_de_muestra(f_local: List[Dict], f_visita: List[Dict]) -> float:
    """Media de goles POR EQUIPO Y PARTIDO estimada de la propia muestra.

    Es la pieza clave del modelo Dixon-Coles: las fuerzas de ataque y defensa
    solo tienen sentido RELATIVAS a la liga. Si se usa una constante fija
    (1.35) y la muestra viene de una racha goleadora, los multiplicadores se
    disparan y el modelo predice 5 goles en un clasico. Estimando la base de
    los propios datos, el modelo se auto-normaliza.
    """
    partidos = len(f_local) + len(f_visita)
    if not partidos:
        return MEDIA_GOLES_EQUIPO
    goles = sum(p["gf"] + p["ga"] for p in f_local + f_visita)
    # Cada partido aporta 2 equipos, y en cada equipo se cuentan gf y ga del
    # mismo partido: el total de goles del partido es (gf + ga) / 1 por equipo.
    media = goles / (2.0 * partidos)
    # Se mezcla con la referencia de liga para no volverse loco con 2 partidos.
    peso = min(1.0, partidos / 10.0)
    return max(0.75, (media * peso) + (MEDIA_GOLES_EQUIPO * (1 - peso)))


def _fuerzas(forma_local: List[Dict], forma_visita: List[Dict],
             media: Optional[float] = None) -> Dict[str, float]:
    """Fuerzas de ataque y defensa (estilo Dixon-Coles simplificado).

    ataque  = goles marcados / media     defensa = goles recibidos / media

    Con muestras cortas los ratios se encogen hacia 1 (rendimiento medio) para
    no sobre-reaccionar a un 4-0 suelto, y se limitan a un rango de cordura:
    un multiplicador de 3x significaria que el equipo mete el triple que la
    media de la liga, que no ocurre en futbol profesional.
    """
    pl = _promedios(forma_local)
    pv = _promedios(forma_visita)
    base = media if media is not None else _media_de_muestra(forma_local, forma_visita)

    # Encogimiento hacia la media: peso = n / (n + k). Con 6 partidos pesa 0.6,
    # con 1 partido pesa 0.2.
    k = 4.0
    wl = pl["n"] / (pl["n"] + k) if pl["n"] else 0.0
    wv = pv["n"] / (pv["n"] + k) if pv["n"] else 0.0

    def _encoger(obs: float) -> float:
        """Ratio observado -> ratio encogido, con tope [0.55, 1.85]."""
        return max(0.55, min(1.85, obs))

    atk_l = _encoger(1.0 + wl * ((pl["gf"] / base) - 1.0)) if pl["n"] else 1.0
    def_l = _encoger(1.0 + wl * ((pl["ga"] / base) - 1.0)) if pl["n"] else 1.0
    atk_v = _encoger(1.0 + wv * ((pv["gf"] / base) - 1.0)) if pv["n"] else 1.0
    def_v = _encoger(1.0 + wv * ((pv["ga"] / base) - 1.0)) if pv["n"] else 1.0

    return {
        "ataque_local": round(atk_l, 3),
        "defensa_local": round(def_l, 3),
        "ataque_visita": round(atk_v, 3),
        "defensa_visita": round(def_v, 3),
        "media_base": round(base, 3),
        "n_local": pl["n"],
        "n_visita": pv["n"],
        "gf_local": round(pl["gf"], 2),
        "ga_local": round(pl["ga"], 2),
        "gf_visita": round(pv["gf"], 2),
        "ga_visita": round(pv["ga"], 2),
    }


# --------------------------------------------------------------------------
# MODELO POISSON
# --------------------------------------------------------------------------
def _poisson(k: int, lam: float) -> float:
    """P(X = k) con media lam."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def goles_esperados(f: Dict[str, float]) -> Dict[str, float]:
    """Goles esperados de cada equipo a partir de las fuerzas."""
    base = f.get("media_base") or MEDIA_GOLES_EQUIPO
    lh = base * f["ataque_local"] * f["defensa_visita"] * VENTAJA_LOCAL
    la = base * f["ataque_visita"] * f["defensa_local"]

    # Techo de cordura: un partido de futbol con mas de 5 goles esperados es
    # sintoma de multiplicadores disparados, no de un partido real. Se reparte
    # el exceso proporcionalmente para no romper la diferencia entre equipos.
    total = lh + la
    if total > 5.0:
        factor = 5.0 / total
        lh, la = lh * factor, la * factor

    return {"local": round(min(max(lh, 0.15), 4.5), 3),
            "visita": round(min(max(la, 0.15), 4.5), 3)}


def distribucion(lh: float, la: float) -> Dict[str, Any]:
    """Distribucion de marcadores y mercados derivados."""
    ph = [_poisson(i, lh) for i in range(MAX_GOLES + 1)]
    pa = [_poisson(j, la) for j in range(MAX_GOLES + 1)]

    p1 = px = p2 = 0.0
    btts = 0.0
    over = {2.5: 0.0, 1.5: 0.0, 3.5: 0.0}
    marcadores = []
    for i in range(MAX_GOLES + 1):
        for j in range(MAX_GOLES + 1):
            p = ph[i] * pa[j]
            if p < 1e-6:
                continue
            if i > j:
                p1 += p
            elif i == j:
                px += p
            else:
                p2 += p
            if i > 0 and j > 0:
                btts += p
            for linea in over:
                if i + j > linea:
                    over[linea] += p
            marcadores.append({"marcador": f"{i}-{j}", "prob": p})

    marcadores.sort(key=lambda m: m["prob"], reverse=True)
    total = p1 + px + p2
    if total > 0:  # normaliza el residuo de la cola truncada
        p1, px, p2 = p1 / total, px / total, p2 / total

    return {
        "local": p1,
        "empate": px,
        "visita": p2,
        "btts": btts,
        "over": {str(k): v for k, v in over.items()},
        "marcadores": marcadores[:5],
        "total_goles_esperado": round(lh + la, 2),
    }


def _prob_handicap(ph: List[float], pa: List[float], linea: float,
                   lado: str) -> float:
    """Probabilidad de cubrir un handicap (linea sobre el equipo elegido).

    Handicap -1.5 => el equipo debe ganar por 2 o mas.
    Handicap +1.5 => pierde por 1, empata o gana.
    Lineas .25/.75 se resuelven como media entre las dos vecinas.
    """
    def _cubrir(margen_ajuste: float) -> float:
        p = 0.0
        for i in range(MAX_GOLES + 1):
            for j in range(MAX_GOLES + 1):
                margen = (i - j) if lado == "local" else (j - i)
                if margen + margen_ajuste > 0:
                    p += ph[i] * pa[j]
        return p

    if linea % 1 in (0.25, 0.75):
        abajo = math.floor(linea * 2) / 2
        arriba = math.ceil(linea * 2) / 2
        return (_cubrir(-abajo) + _cubrir(-arriba)) / 2
    return _cubrir(-linea)


# --------------------------------------------------------------------------
# LECTURA DEL MERCADO DEL PICK
# --------------------------------------------------------------------------
def _parecido(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    wa = {w for w in a.replace("-", " ").split() if len(w) > 2}
    wb = {w for w in b.replace("-", " ").split() if len(w) > 2}
    return bool(wa & wb)


def _linea(texto: str) -> Optional[float]:
    """Saca la linea numerica del texto ('Over 2.5' -> 2.5, 'Handicap -1' -> -1)."""
    import re
    m = re.search(r"([+-]?\d+(?:[.,]\d+)?)", texto or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


# Mercados que el modelo Poisson NO puede resolver. Se declaran abiertamente en
# vez de inventar una probabilidad: un numero falso seria peor que no analizar.
_NO_SOPORTADO = (
    ("primera mitad", "el modelo no divide el partido por tiempos"),
    ("1ª mitad", "el modelo no divide el partido por tiempos"),
    ("2ª mitad", "el modelo no divide el partido por tiempos"),
    ("segunda mitad", "el modelo no divide el partido por tiempos"),
    ("corner", "ESPN no publica historico de corners por equipo"),
    ("tarjeta", "ESPN no publica historico de tarjetas por equipo"),
    ("falta", "ESPN no publica historico de faltas por equipo"),
    ("tiro", "ESPN no publica historico de tiros por equipo"),
    ("jugador", "los props de jugador necesitan stats individuales historicas"),
    ("primer punto", "mercado de tenis sin fuente gratuita"),
    ("set", "mercado de tenis sin fuente gratuita"),
    ("juego", "mercado de tenis sin fuente gratuita"),
)


def clasificar_mercado(market: str, selection: str, titulo: str = "") -> Dict[str, Any]:
    """Traduce el texto del pick a un mercado que el modelo pueda resolver."""
    texto = " ".join(x for x in (market, titulo, selection) if x).lower()

    for clave, motivo in _NO_SOPORTADO:
        if clave in texto:
            return {"tipo": "no_soportado", "motivo": motivo}

    if "ambos marcan" in texto or "both teams" in texto or "btts" in texto:
        return {"tipo": "btts", "lado": "si" if "no " not in texto[:12] else "no"}

    if "handicap" in texto or "hándicap" in texto or "spread" in texto:
        linea = _linea(selection) or _linea(market) or _linea(titulo)
        return {"tipo": "handicap", "linea": linea if linea is not None else 0.0}

    if ("total" in texto or "over" in texto or "under" in texto
            or "mas de" in texto or "más de" in texto or "menos de" in texto):
        linea = _linea(selection) or _linea(market) or _linea(titulo)
        negativo = ("under" in texto or "menos de" in texto)
        return {"tipo": "total", "linea": linea, "lado": "under" if negativo else "over"}

    return {"tipo": "ganador"}


def prob_del_pick(pick: Dict[str, Any], dist: Dict[str, Any],
                  lambdas: Dict[str, float]) -> Dict[str, Any]:
    """Probabilidad JUSTA del pick segun el modelo. Indica si esta soportado."""
    home = pick.get("home_name") or ""
    away = pick.get("away_name") or ""
    market = pick.get("market") or ""
    selection = pick.get("selection") or ""
    titulo = pick.get("titulo") or ""

    m = clasificar_mercado(market, selection, titulo)
    tipo = m["tipo"]

    if tipo == "no_soportado":
        return {"soportado": False, "motivo": m["motivo"], "tipo": tipo}

    ph = [_poisson(i, lambdas["local"]) for i in range(MAX_GOLES + 1)]
    pa = [_poisson(j, lambdas["visita"]) for j in range(MAX_GOLES + 1)]

    if tipo == "ganador":
        if _parecido(selection, home) or _parecido(titulo, home):
            return {"soportado": True, "tipo": tipo, "lado": "local", "prob": dist["local"]}
        if _parecido(selection, away) or _parecido(titulo, away):
            return {"soportado": True, "tipo": tipo, "lado": "visita", "prob": dist["visita"]}
        sel = (selection or "").strip().upper()
        if sel == "1":
            return {"soportado": True, "tipo": tipo, "lado": "local", "prob": dist["local"]}
        if sel == "2":
            return {"soportado": True, "tipo": tipo, "lado": "visita", "prob": dist["visita"]}
        if sel == "X" or "empate" in (selection or "").lower() or "draw" in (selection or "").lower():
            return {"soportado": True, "tipo": tipo, "lado": "empate", "prob": dist["empate"]}
        return {"soportado": False, "tipo": tipo,
                "motivo": "no se pudo identificar el equipo elegido"}

    if tipo == "handicap":
        linea = m["linea"] if m["linea"] is not None else 0.0
        # El signo depende de a quien se aplica: si la seleccion nombra al
        # visitante, la linea del texto va sobre el.
        if _parecido(selection, away) or _parecido(titulo, away):
            lado = "visita"
        else:
            lado = "local"
        return {"soportado": True, "tipo": tipo, "lado": lado, "linea": linea,
                "prob": _prob_handicap(ph, pa, linea, lado)}

    if tipo == "total":
        linea = m["linea"]
        if linea is None:
            return {"soportado": False, "tipo": tipo,
                    "motivo": "el pick no indica la linea de goles"}
        p_over = 0.0
        for i in range(MAX_GOLES + 1):
            for j in range(MAX_GOLES + 1):
                if i + j > linea:
                    p_over += ph[i] * pa[j]
        return {"soportado": True, "tipo": tipo, "lado": m["lado"], "linea": linea,
                "prob": p_over if m["lado"] == "over" else (1.0 - p_over)}

    if tipo == "btts":
        p = dist["btts"]
        return {"soportado": True, "tipo": tipo, "lado": m["lado"],
                "prob": p if m["lado"] == "si" else (1.0 - p)}

    return {"soportado": False, "tipo": tipo, "motivo": "mercado no reconocido"}


# --------------------------------------------------------------------------
# ANALISIS
# --------------------------------------------------------------------------
def _veredicto(ev: float) -> str:
    """VALOR si el precio paga de mas, EVITAR si paga de menos."""
    if ev >= 0.05:
        return "VALOR"
    if ev <= -0.05:
        return "EVITAR"
    return "NEUTRO"


def _confianza(n_local: int, n_visita: int, ev: float) -> str:
    """Cuanta evidencia respalda el veredicto. Muestra corta => confianza baja."""
    muestra = min(n_local, n_visita)
    if muestra >= 5 and abs(ev) >= 0.08:
        return "ALTA"
    if muestra >= 3:
        return "MEDIA"
    return "BAJA"


def _norm_pick(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Acepta el pick en snake_case (BD cruda) o camelCase (API publica)."""
    if not pick:
        return {}
    alias = {
        "home_name": "homeName", "away_name": "awayName",
        "sport_label": "sportLabel", "event_id": "eventId",
    }
    out = dict(pick)
    for snake, camel in alias.items():
        if not out.get(snake) and out.get(camel):
            out[snake] = out[camel]
    return out


def analizar_pick(pick: Dict[str, Any], con_llm: bool = False) -> Dict[str, Any]:
    """Analiza UN pick. Capa 1 cuantitativa siempre; capa 2 LLM si se pide.

    Nunca lanza excepcion: devuelve el analisis con 'ok' y 'motivo' cuando no
    se puede hacer, para que el dashboard muestre la razon real.
    """
    pick = _norm_pick(pick)
    sport = pick.get("sport") or ""
    league = pick.get("league") or ""
    home = pick.get("home_name") or pick.get("home") or ""
    away = pick.get("away_name") or pick.get("away") or ""

    if not home or not away:
        return {"ok": False, "motivo": "el pick no tiene los dos equipos"}

    if not _es_soccer(sport, league):
        return {
            "ok": False,
            "motivo": ("el modelo cuantitativo es de goles (Poisson) y este pick "
                       f"es de {sport or 'otro deporte'}"),
        }

    f_local = forma_equipo(sport, league, home, n=6)
    f_visita = forma_equipo(sport, league, away, n=6)
    if not f_local and not f_visita:
        return {"ok": False, "motivo": "ESPN no devolvio forma reciente de ningun equipo"}

    fuerzas = _fuerzas(f_local, f_visita)
    lambdas = goles_esperados(fuerzas)
    dist = distribucion(lambdas["local"], lambdas["visita"])
    pp = prob_del_pick(pick, dist, lambdas)

    salida: Dict[str, Any] = {
        "ok": True,
        "pick_id": pick.get("id"),
        "partido": f"{home} vs {away}",
        "soportado": pp.get("soportado", False),
        "mercado_detectado": pp.get("tipo"),
        "lado": pp.get("lado"),
        "linea": pp.get("linea"),
        "forma_local": f_local,
        "forma_visita": f_visita,
        "fuerzas": fuerzas,
        "goles_esperados": lambdas,
        "limitaciones": [
            "La forma viene de los ultimos 6 partidos de ESPN (todas las competiciones).",
            "No ajusta por calidad del rival: una racha contra equipos debiles infla el ataque.",
            "No conoce lesiones, sanciones ni alineaciones.",
            "Solo modela mercados de goles (1X2, totales, handicap, ambos marcan).",
        ],
        "distribucion": {
            "local": round(dist["local"], 4),
            "empate": round(dist["empate"], 4),
            "visita": round(dist["visita"], 4),
            "ambos_marcan": round(dist["btts"], 4),
            "total_goles_esperado": dist["total_goles_esperado"],
            "marcadores_mas_probables": [
                {"marcador": m["marcador"], "prob": round(m["prob"], 4)}
                for m in dist["marcadores"]
            ],
        },
    }

    if not pp.get("soportado"):
        salida["motivo"] = pp.get("motivo")
        return salida

    try:
        cuota = float(pick.get("odds") or 0)
    except (TypeError, ValueError):
        cuota = 0.0

    prob = float(pp["prob"])
    salida["prob_justa"] = round(prob, 4)
    salida["cuota"] = cuota

    if cuota > 1.0:
        implicita = 1.0 / cuota
        ev = prob * cuota - 1.0
        salida["prob_implicita"] = round(implicita, 4)
        salida["edge_pct"] = round((prob - implicita) * 100, 2)
        salida["ev_pct"] = round(ev * 100, 2)
        salida["veredicto"] = _veredicto(ev)
        salida["confianza"] = _confianza(
            fuerzas["n_local"], fuerzas["n_visita"], ev)
    else:
        salida["veredicto"] = "SIN_CUOTA"
        salida["confianza"] = "BAJA"
        salida["nota"] = "el pick no trae cuota: no se puede calcular el valor"

    if con_llm:
        texto, modelo = _informe_llm(salida, pick)
        salida["informe_llm"] = texto
        salida["modelo_llm"] = modelo

    return salida


# --------------------------------------------------------------------------
# CAPA 2: LLM (proveedor PROPIO, distinto al del chat)
# --------------------------------------------------------------------------
def _llamar(mensaje: str, system: str) -> tuple:
    """Llama al proveedor del analista. Devuelve (texto, modelo) o (None, None)."""
    if not KEYS:
        return None, None

    modelos = [m for m in (MODELO, MODELO_FALLBACK) if m]
    ultimo = ""
    ahora = time.time()
    for clave in KEYS:
        if ahora < _CLAVES_EN_PAUSA.get(clave, 0):
            continue  # esta cuenta agoto su cupo hace poco: no gastar intentos
        clave_agotada = False
        for modelo in modelos:
            payload = {
                "model": modelo,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": mensaje},
                ],
                "max_tokens": MAX_TOKENS,
                "temperature": 0.2,
            }
            if "openrouter.ai" in BASE_URL:
                # Acota el razonamiento interno: sin esto el modelo puede gastar
                # todo el presupuesto y devolver el informe vacio.
                payload["reasoning"] = {"effort": REASONING}
            try:
                r = requests.post(
                    BASE_URL,
                    headers={
                        "Authorization": f"Bearer {clave}",
                        "Content-Type": "application/json",
                        # OpenRouter pide identificarse; sin esto algunas rutas dan 403.
                        "HTTP-Referer": "https://threesixtybets.com",
                        "X-Title": "3SIXTYBETS Analista",
                    },
                    json=payload,
                    timeout=TIMEOUT,
                )
            except Exception as e:  # noqa: BLE001
                ultimo = f"red: {e}"
                print(f"[Analista-LLM] {ultimo}", flush=True)
                continue

            if r.status_code != 200:
                ultimo = f"{modelo} {r.status_code}: {r.text[:160]}"
                print(f"[Analista-LLM] {ultimo}", flush=True)
                # Cuota o credencial de ESTA cuenta: otra clave = otra cuota.
                # Se pausa la clave un rato y se pasa a la siguiente sin dormir.
                if r.status_code in (401, 402, 403) or (
                    r.status_code == 429 and sin_cuota(r.text)
                ):
                    _CLAVES_EN_PAUSA[clave] = time.time() + _PAUSA_CLAVE_S
                    clave_agotada = True
                    break
                continue

            try:
                opciones = r.json().get("choices") or []
                texto = ((opciones[0].get("message") or {}).get("content") or "").strip()
            except Exception:  # noqa: BLE001
                texto = ""
            if texto:
                return texto, modelo
            ultimo = f"{modelo} devolvio contenido vacio"
        if not clave_agotada:
            break

    print(f"[Analista-LLM] sin respuesta: {ultimo}", flush=True)
    return None, None


def _prompt_analisis(a: Dict[str, Any], pick: Dict[str, Any]) -> str:
    """Prompt con SOLO los datos calculados: el modelo no debe inventar cifras."""
    f = a.get("fuerzas") or {}
    d = a.get("distribucion") or {}
    ge = a.get("goles_esperados") or {}
    lineas = [
        f"PARTIDO: {a.get('partido')}",
        f"PICK A REVISAR: {pick.get('titulo') or pick.get('market')} -> "
        f"{pick.get('selection')} (cuota {a.get('cuota')})",
        "",
        "MODELO CUANTITATIVO (Poisson sobre la forma real de ESPN):",
        f"  Goles a favor por partido: {f.get('gf_local')} y {f.get('gf_visita')}",
        f"  Goles en contra por partido: {f.get('ga_local')} y {f.get('ga_visita')}",
        f"  Muestra: {f.get('n_local')} y {f.get('n_visita')} partidos",
        f"  Marcador esperado: {ge.get('local')} - {ge.get('visita')}",
        f"  Probabilidad 1X2: {d.get('local')} / {d.get('empate')} / {d.get('visita')}",
        f"  Probabilidad del pick: {a.get('prob_justa')}",
        f"  Probabilidad implicita de la cuota: {a.get('prob_implicita')}",
        f"  EDGE: {a.get('edge_pct')}%   EV: {a.get('ev_pct')}%",
        f"  Veredicto del modelo: {a.get('veredicto')} (confianza {a.get('confianza')})",
        "",
        "Redacta en espanol, maximo 120 palabras, sin tablas ni markdown:",
        "1) que dice el modelo y por que;",
        "2) si la cuota paga de mas o de menos;",
        "3) recomendacion final en una frase.",
        "Usa SOLO las cifras de arriba. No inventes estadisticas ni nombres.",
    ]
    return "\n".join(lineas)


def _informe_llm(a: Dict[str, Any], pick: Dict[str, Any]) -> tuple:
    """Redacta el informe. (None, None) si no hay key o el proveedor falla."""
    system = (
        "Eres un analista cuantitativo de apuestas deportivas. Tu trabajo es "
        "explicar el resultado de un modelo estadistico, no crear datos nuevos. "
        "Si falta informacion, lo dices. Nunca prometes ganancias."
    )
    return _llamar(_prompt_analisis(a, pick), system)


# --------------------------------------------------------------------------
# SALIDAS DE ALTO NIVEL
# --------------------------------------------------------------------------
def informe_texto(a: Dict[str, Any]) -> str:
    """Version legible del analisis, para logs o para devolver al frontend."""
    if not a.get("ok"):
        return f"Sin analisis: {a.get('motivo')}"
    if not a.get("soportado"):
        return (f"{a.get('partido')}: mercado '{a.get('mercado_detectado')}' no "
                f"modelable ({a.get('motivo')})")

    L = [
        f"{a.get('partido')}  [{a.get('veredicto')} / confianza {a.get('confianza')}]",
        f"  Probabilidad justa del modelo: {a.get('prob_justa')}",
        f"  Probabilidad de la cuota:      {a.get('prob_implicita')}",
        f"  EDGE {a.get('edge_pct')}%   EV {a.get('ev_pct')}%",
        f"  Goles esperados: {a.get('goles_esperados', {}).get('local')} - "
        f"{a.get('goles_esperados', {}).get('visita')}",
        f"  Muestra: {a.get('fuerzas', {}).get('n_local')} y "
        f"{a.get('fuerzas', {}).get('n_visita')} partidos",
    ]
    if a.get("informe_llm"):
        L.append(f"  Informe: {a['informe_llm']}")
    return "\n".join(L)


def analizar_lote(picks: List[Dict[str, Any]], con_llm: bool = False,
                  limite: Optional[int] = None) -> Dict[str, Any]:
    """Analiza varios picks. Devuelve el resumen y el detalle por pick."""
    lista = picks[:limite] if limite else picks
    detalle = []
    for p in lista:
        try:
            detalle.append(analizar_pick(p, con_llm=con_llm))
        except Exception as e:  # noqa: BLE001 - un pick roto no tumba el lote
            detalle.append({"ok": False, "pick_id": p.get("id"),
                            "motivo": f"error inesperado: {e}"})

    con_veredicto = [d for d in detalle if d.get("veredicto") in
                     ("VALOR", "NEUTRO", "EVITAR")]
    resumen = {
        "analizados": len(detalle),
        "con_modelo": len(con_veredicto),
        "valor": sum(1 for d in con_veredicto if d["veredicto"] == "VALOR"),
        "neutro": sum(1 for d in con_veredicto if d["veredicto"] == "NEUTRO"),
        "evitar": sum(1 for d in con_veredicto if d["veredicto"] == "EVITAR"),
        "no_modelables": sum(1 for d in detalle if d.get("ok") and not d.get("soportado")),
        "sin_datos": sum(1 for d in detalle if not d.get("ok")),
    }
    if con_veredicto:
        resumen["ev_promedio_pct"] = round(
            sum(d.get("ev_pct") or 0 for d in con_veredicto) / len(con_veredicto), 2)

    return {"resumen": resumen, "detalle": detalle,
            "texto": "\n".join(informe_texto(d) for d in detalle)}


def estado() -> Dict[str, Any]:
    """Estado del analista: que proveedor usa y si la capa 2 esta activa."""
    return {
        "capa_cuantitativa": "activa (no necesita API key)",
        "capa_llm": "activa" if KEYS else "inactiva (falta ANALISTA_AI_API_KEY)",
        "claves": len(KEYS),
        "proveedor": BASE_URL,
        "modelo": MODELO,
        "modelo_fallback": MODELO_FALLBACK or None,
        "aislado_del_chat": True,
        "deportes_modelados": ["soccer"],
    }


__all__ = [
    "analizar_pick",
    "analizar_lote",
    "informe_texto",
    "estado",
    "forma_equipo",
    "distribucion",
    "goles_esperados",
    "clasificar_mercado",
    "prob_del_pick",
    "equipo_id",
]
