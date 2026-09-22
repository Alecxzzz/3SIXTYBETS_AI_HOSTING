"""
Refrescador AUTOMATICO de los partidos del dia (cuadro "Partidos de hoy" de la TV).

Cada 30 minutos:
1. Descarga la agenda de la fuente (la18hd.su/eventos/json/agenda123.json).
2. Para cada canal de la agenda: abre su pagina y extrae el playbackURL
   fresco. (Los tokens mueren rapido; el m3u8 guardado en la BD es solo
   fallback: la TV resuelve fresco al hacer clic via /event-resolve.)
3. Reemplaza la tabla events. Si la fuente fallo o devolvio vacio, NO
   toca la tabla (para no dejar la TV vacia por una caida temporal).

Arranca como hilo daemon desde el startup de main.py. Asi la TV se
actualiza sola todos los dias, sin depender de la PC de casa.
"""

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import requests

import db

AGENDA_URL = "https://la18hd.su/eventos/json/agenda123.json"
PAGE_URL = "https://la18hd.su/vivo/canales.php?stream={slug}"
PLAYBACK_RE = re.compile(r'var\s+playbackURL\s+=\s+"([^"]*)"')
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

INTERVAL_S = 30 * 60          # refresco cada 30 minutos
PRIMER_DELAY_S = 15           # primera corrida: deja al startup terminar
GRACIA_MIN = 150              # ventana "en vivo" de la fuente (+2.5h tras el inicio)

LIMA = timezone(timedelta(hours=-5))  # la agenda usa hora Peru (America/Lima)

# Nombres lindos de canal para la lista de la TV. Lo que no esta aqui
# cae al formato generico (espn5 -> "ESPN 5").
CANAL_NOMBRES = {
    "espn": "ESPN",
    "espnmx": "ESPN MX",
    "espn2mx": "ESPN 2 MX",
    "espn4mx": "ESPN 4 MX",
    "espnpremium": "ESPN Premium",
    "espnndeportes": "ESPN Deportes",
    "espn2": "ESPN 2",
    "espn3": "ESPN 3",
    "espn4": "ESPN 4",
    "espn5": "ESPN 5",
    "espn7": "ESPN 7",
    "dsports": "DSports",
    "dsports2": "DSports 2",
    "tntsports": "TNT Sports",
    "tntsportschile": "TNT Sports Chile",
    "tudn": "TUDN",
    "telemundo": "Telemundo",
    "universo": "Universo",
    "canal5": "Canal 5",
    "foxsports2": "Fox Sports 2",
    "foxsports3": "Fox Sports 3",
    "winsports2": "Win Sports+",
    "vtvplus": "VTV Plus",
    "max2": "Max 2",
    "liga1max": "Liga 1 Max",
    "premiere1": "Premiere",
    "premiere2": "Premiere 2",
    "premiere3": "Premiere 3",
    "fanatiz6": "Fanatiz 6",
    "fanatiz10": "Fanatiz 10",
    "beinsportes": "beIN Sports",
    "hypermotion1": "Laliga Hypermotion",
}


def _canal_pretty(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if slug in CANAL_NOMBRES:
        return CANAL_NOMBRES[slug]
    base = re.sub(r"(\d+)$", r" \1", slug)
    return base.replace("_", " ").upper()


def _vigente(fecha: str, hora: str) -> bool:
    """True si el partido no termino (inicio + gracia > ahora, hora Lima)."""
    try:
        inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=LIMA)
        return inicio + timedelta(minutes=GRACIA_MIN) > datetime.now(LIMA)
    except Exception:
        return True  # fecha/hora raras: no lo descartamos por eso


def _stream_vivo(url: str, referer: str) -> bool:
    """True si el m3u8 responde 200 con contenido HLS.

    La fuente (fubo18) rota el subdominio en cada request: algunos hosts
    estan muertos (DNS fail, timeout, 404). Sin esta validacion la TV
    publica eventos con streams muertos y TODOS los partidos dan
    manifestLoadError al abrirlos.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": UA, "Referer": referer},
            timeout=10,
        )
        return resp.status_code == 200 and "#EXTM3U" in resp.text
    except Exception:
        return False


def _procesar_evento(ev: dict):
    """Scrapea un evento de la agenda y devuelve el dict del evento o None.

    El host del stream ROTA en cada request y algunos hosts estan muertos
    (DNS fail / timeout / 404): re-scrapeamos hasta 3 veces y solo aceptamos
    el token cuyo m3u8 realmente responde. Sin esta validacion la TV
    publica canales muertos y TODOS los partidos dan manifestLoadError.
    """
    link = (ev.get("link") or "").strip()
    if "la18hd.su" not in link:
        return None  # tarjetarojita.xyz y otros espejos: solo la fuente principal
    if not _vigente(ev.get("date") or "", ev.get("time") or ""):
        return None
    slug = parse_qs(urlparse(link).query).get("stream", [None])[0]
    if not slug:
        return None
    page = PAGE_URL.format(slug=slug)

    playback = None
    for _intento in range(3):
        try:
            resp = requests.get(
                page,
                headers={"User-Agent": UA, "Referer": "https://la18hd.su/eventos/"},
                timeout=20,
            )
        except Exception:
            continue
        m = PLAYBACK_RE.search(resp.text)
        if not m:
            continue
        playback = m.group(1)
        if _stream_vivo(playback, page):
            break
        playback = None  # host muerto: forzar otro scrape (nuevo token/host)

    if not playback:
        return None  # este evento no tiene stream vivo ahora mismo

    titulo = " ".join((ev.get("title") or "").split())
    if ":" in titulo:
        sport, name = (x.strip() for x in titulo.split(":", 1))
    else:
        sport, name = "Futbol", titulo
    if not name:
        return None

    label = f"{name} · {_canal_pretty(slug)}"
    lang = (ev.get("language") or "").strip()
    if lang and lang.lower() not in label.lower():
        label += f" ({lang.capitalize()})"

    return {"sport": sport or "Futbol", "name": label, "stream": playback, "referer": page}


def refresh_once() -> str:
    """Una corrida completa. Devuelve un mensaje para el log.

    Los eventos se procesan EN PARALELO (cada uno implica scrape + validacion
    del m3u8, hasta 3 intentos): en serie tardaria ~20 min con muchos streams
    muertos; con 10 hilos baja a ~2 min.
    """
    agenda = requests.get(AGENDA_URL, headers={"User-Agent": UA}, timeout=20).json()
    if not isinstance(agenda, list) or not agenda:
        return "agenda vacia o invalida; se conserva la lista actual"

    from concurrent.futures import ThreadPoolExecutor

    events = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for resultado in pool.map(_procesar_evento, agenda):
            if resultado:
                events.append(resultado)

    if not events:
        return "no se scrapeo ningun stream vivo; se conserva la lista actual"

    count = db.replace_events(events)
    return f"{count} evento(s) publicados (agenda: {len(agenda)} items)"


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
