"""
Refrescador AUTOMATICO de la agenda de partidos (cuadro "Partidos de hoy").

Antes leia la18hd.su/eventos/json/agenda123.json, que **esta caida** (timeout).
Ahora la agenda se arma con fuentes que ya usa el backend:

1. ESPN (site.api.espn.com): hora, equipos, liga y el canal del partido
   (broadcasts / geoBroadcasts) para tenis (ATP/WTA), NBA, NFL y futbol.
2. dlive.sx: canal de streaming real por partido (main.dlive_buscar_canales).

Cada fila de la tabla `events` guarda el destino YA resuelto:
  - canal del partido presente y ONLINE en el catalogo de cdnlivetv
      -> {BASE}/tv/cdnlivetv/{canal}/{code}   (type=m3u8; el backend resuelve
         el token fresco y lo sirve por /hls-proxy)
  - si no, y el partido esta en la agenda de dlive
      -> pagina del canal en dlive            (type=iframe)
  - si no hay destino jugable, el evento NO se guarda: la TV solo muestra lo
    que se puede ver de verdad.

Corre cada 30 min desde el startup de main.py. Si una fuente falla o no hay
ningun evento jugable, NO se toca la tabla (se conserva la lista anterior).
"""
import os
import re
import threading
import time
from urllib.parse import quote

import requests

import db

INTERVAL_S = 30 * 60          # refresco cada 30 minutos
PRIMER_DELAY_S = 30           # deja terminar el startup antes de la 1a corrida

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# (ruta de ESPN, etiqueta que se pinta en la tarjeta)
ESPN_FUENTES = (
    ("tennis/atp", "TENIS"),
    ("tennis/wta", "TENIS"),
    ("basketball/nba", "NBA"),
    ("football/nfl", "NFL"),
    ("soccer/esp.1", "LALIGA"),
    ("soccer/eng.1", "PREMIER LEAGUE"),
    ("soccer/ita.1", "SERIE A"),
    ("soccer/ger.1", "BUNDESLIGA"),
    ("soccer/fra.1", "LIGUE 1"),
    ("soccer/por.1", "PRIMEIRA LIGA"),
    ("soccer/ned.1", "EREDIVISIE"),
    ("soccer/mex.1", "LIGA MX"),
    ("soccer/arg.1", "LIGA ARGENTINA"),
    ("soccer/usa.1", "MLS"),
    ("soccer/bra.1", "BRASILEIRAO"),
    ("soccer/uefa.champions", "CHAMPIONS LEAGUE"),
    ("soccer/uefa.europa", "EUROPA LEAGUE"),
)

# --- Normalizacion de nombres de canal -------------------------------------
# ESPN da "ESPN Deportes", "MNMT", "NFL Net"; el catalogo de cdnlivetv usa
# "ESPN Deportes US", "Monumental Sports Network US", "NFL Network HD US".
# Se comparan por tokens: todas las palabras relevantes del nombre deben
# aparecer en el del catalogo (y si uno nombra el pais, debe coincidir).
CALIDAD = {"fhd", "hd", "uhd", "sd", "4k"}
PAISES = {
    "us", "ca", "uk", "br", "arg", "mx", "pt", "pl", "fr", "de", "nl", "nz",
    "il", "gr", "dk", "se", "fi", "at", "au", "cy", "bg", "ru", "tr", "ksa", "ro",
}
RELLENO = {"the", "tv", "channel", "network", "sports", "sport", "live"}
# ESPN abrevia ("MNMT", "NFL Net", "USA Net", "NBCS Bay Area") y el catalogo usa
# el nombre largo. Se normaliza token a token antes de comparar.
ALIAS_CANAL = {
    "net": "network",
    "mnmt": "monumental",
    "nbcs": "nbc sports",
    "espn2": "espn 2",
    "espn3": "espn 3",
    "espn4": "espn 4",
    "cbssn": "cbs sports network",
    "fs1": "fox sports 1",
    "fs2": "fox sports 2",
    "tnt": "tnt",
    "uni": "universo",
}
ALIAS_PAIS = {
    "poland": "pl", "israel": "il", "england": "uk", "germany": "de",
    "france": "fr", "turkey": "tr", "greece": "gr", "portugal": "pt",
    "netherlands": "nl", "denmark": "dk", "sweden": "se", "finland": "fi",
    "canada": "ca", "brazil": "br", "argentina": "arg", "mexico": "mx",
    "spain": "es", "italy": "it", "cyprus": "cy", "romania": "ro",
    "bulgaria": "bg", "usa": "us",
}


def _tokens(txt: str):
    txt = (txt or "").lower()
    txt = re.sub(r"([a-z])(\d)", r"\1 \2", txt)
    txt = re.sub(r"(\d)([a-z])", r"\1 \2", txt)
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    salida = []
    for t in txt.split():
        t = ALIAS_PAIS.get(t, t)
        # ALIAS_CANAL puede devolver varias palabras ("nbcs" -> "nbc sports").
        for parte in str(ALIAS_CANAL.get(t, t)).split():
            if parte:
                salida.append(parte)
    return salida


def _canal_cdn(nombre: str, catalogo: list):
    """Canal del catalogo que corresponde a ese nombre (prefiere los online)."""
    tokens = set(_tokens(nombre))
    claves = {
        t for t in tokens
        if t not in CALIDAD and t not in PAISES and t not in RELLENO
    }
    if not claves:
        return None
    paises = tokens & PAISES
    candidatos = []
    for c in catalogo:
        ct = set(_tokens(c.get("name")))
        if not claves <= ct:
            continue
        if paises and not (paises & ct):
            continue
        candidatos.append((len(ct - tokens), not c.get("online"), c))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: (x[0], x[1], x[2].get("name") or ""))
    return candidatos[0][2]


def _base_url() -> str:
    """URL publica del backend, para poder apuntar a /tv/cdnlivetv."""
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        host = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        base = f"https://{host}" if host else "http://localhost:8000"
    return base



def _catalogo_normalizado() -> list:
    """Catalogo de cdnlivetv con `online` booleano.

    `cdnlivetv_catalogo()` devuelve `status` ("online"/"offline"); aqui se
    normaliza para que el resto del codigo hable de `online` y `name`.
    """
    try:
        from main import cdnlivetv_catalogo

        crudo = cdnlivetv_catalogo() or []
    except Exception as exc:
        print(f"[EventsScheduler] sin catalogo CDN: {exc!r}", flush=True)
        return []
    return [
        {
            "name": c.get("name") or "",
            "code": c.get("code") or "us",
            "online": (c.get("status") or "").strip().lower() == "online",
        }
        for c in crudo
        if c.get("name")
    ]


def _eventos_espn() -> list:
    """Partidos del dia con su canal segun ESPN (las 4 disciplinas)."""
    salida = []
    for path, liga in ESPN_FUENTES:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=20,
            )
            if not r.ok:
                continue
            data = r.json() or {}
        except Exception as exc:
            print(f"[EventsScheduler] ESPN {path} fallo: {exc}", flush=True)
            continue

        for ev in data.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            equipos = {}
            for c in comp.get("competitors") or []:
                equipos[c.get("homeAway")] = (c.get("team") or {}).get("displayName")
            home = (equipos.get("home") or "").strip()
            away = (equipos.get("away") or "").strip()
            nombre = f"{home} vs {away}" if home and away else (ev.get("name") or "")
            nombre = nombre.strip()
            if not nombre:
                continue

            canales = []
            for b in comp.get("broadcasts") or []:
                canales += [n for n in (b.get("names") or []) if n]
            for g in comp.get("geoBroadcasts") or []:
                n = ((g.get("media") or {}).get("shortName") or "").strip()
                if n:
                    canales.append(n)
            unicos = []
            for n in canales:
                if n and n not in unicos:
                    unicos.append(n)

            salida.append({
                "liga": liga,
                "name": nombre,
                "home": home,
                "away": away,
                "fecha": ev.get("date") or "",
                "canales": unicos,
            })
    return salida


def _destino(ev: dict, catalogo: list, base: str):
    """(url, tipo, etiqueta) jugable para ese partido, o (None, None, None)."""
    # 1) El canal que da ESPN, si existe y esta online en el CDN.
    for nombre in ev["canales"]:
        canal = _canal_cdn(nombre, catalogo)
        if not canal or not canal.get("online"):
            continue
        code = canal.get("code") or "us"
        url = f"{base}/tv/cdnlivetv/{quote(canal['name'], safe='')}/{code}"
        return url, "m3u8", canal["name"]

    # 2) El canal real segun la agenda de dlive (su player va por iframe).
    try:
        from main import dlive_buscar_canales

        canales = dlive_buscar_canales(ev["home"], ev["away"], ev["fecha"])
    except Exception as exc:
        print(f"[EventsScheduler] dlive fallo para {ev['name']}: {exc!r}", flush=True)
        canales = None
    for c in canales or []:
        if c.get("url"):
            return c["url"], "iframe", c.get("nombre") or "dlive"

    return None, None, None


def refresh_once() -> str:
    """Una corrida completa. Devuelve un mensaje para el log."""
    base = _base_url()
    catalogo = _catalogo_normalizado()

    agenda = _eventos_espn()
    if not agenda:
        return "ESPN no devolvio partidos; se conserva la lista actual"

    eventos, sin_destino = [], 0
    for ev in agenda:
        url, tipo, etiqueta = _destino(ev, catalogo, base)
        if not url:
            sin_destino += 1
            continue
        eventos.append({
            "sport": ev["liga"],
            "name": f"{ev['name']} · {etiqueta}"[:200],
            "stream": url,
            "type": tipo,
            "referer": None,
        })

    if not eventos:
        return (f"0 eventos jugables de {len(agenda)} partidos; "
                "se conserva la lista actual")

    count = db.replace_events(eventos)
    return (f"{count} evento(s) publicados de {len(agenda)} partidos "
            f"({sin_destino} sin canal jugable)")


def _loop() -> None:
    time.sleep(PRIMER_DELAY_S)
    while True:
        try:
            print(f"[EventsScheduler] {refresh_once()}", flush=True)
        except Exception as exc:
            print(f"[EventsScheduler] error: {exc!r}", flush=True)
        time.sleep(INTERVAL_S)


def iniciar_scheduler() -> None:
    threading.Thread(target=_loop, daemon=True, name="event-scheduler").start()


if __name__ == "__main__":
    import sys

    if "--dry" in sys.argv:
        # Muestra la agenda SIN tocar la BD (para ver que se va a publicar).
        base = _base_url()
        catalogo = _catalogo_normalizado()
        agenda = _eventos_espn()
        print(f"base={base}  partidos ESPN={len(agenda)}  "
              f"catalogo={len(catalogo)}\n")
        jugables = 0
        for ev in agenda:
            url, tipo, etiqueta = _destino(ev, catalogo, base)
            if url:
                jugables += 1
            print(f"{ev['liga']:<17} {ev['name'][:42]:<42} "
                  f"{(etiqueta or '-')[:26]:<26} {tipo or ''}")
        print(f"\njugables: {jugables}/{len(agenda)}")
    else:
        print(refresh_once())
