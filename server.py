import asyncio
import atexit
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

HOST, PORT = "127.0.0.1", 8000
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
UA = "Mozilla/5.0"
RETRIES = 3
M3U8 = "application/vnd.apple.mpegurl"
NOCACHE = {"Cache-Control": "no-cache, no-store"}
FFMPEG = shutil.which("ffmpeg")
RX_DIR = Path(tempfile.gettempdir()) / "iptv_remux"

# Tras este nº de caídas de ffmpeg en 60 s se declara el canal inservible y se
# detiene el reaper (antes se reiniciaba cada 5 s indefinidamente = crash-loop).

http = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, read=25.0),
    follow_redirects=True,
    headers={"User-Agent": UA},
    limits=httpx.Limits(max_connections=8, keepalive_expiry=5),
)
playlists: OrderedDict = OrderedDict()  # url -> (ts, texto reescrito)
segments: OrderedDict = OrderedDict()   # url -> (bytes, content-type)
stats = {"ok": 0, "retried": 0, "failed": 0, "cache_hits": 0, "stale_playlists": 0}
recent_failures: deque = deque(maxlen=15)
rx_lock = threading.RLock()
rx = {"proc": None, "log": None, "url": None, "mode": None, "last": 0.0, "started": 0.0,
      "relaunches": 0, "crashes": 0, "crash_window": 0.0, "dead": False}


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def fail(msg: str):
    stats["failed"] += 1
    recent_failures.append(f"{time.strftime('%H:%M:%S')} {msg}")
    log("FAIL " + msg)


def lru_put(d: OrderedDict, key, value, limit: int):
    d[key] = value
    d.move_to_end(key)
    while len(d) > limit:
        d.popitem(last=False)


# ---------------------------------------------------------------- playlists

def prox(base: str, ref: str) -> str:
    return "/proxy?url=" + quote(urljoin(base, ref), safe="")


def rewrite(text: str, base: str) -> str:
    """Reescribe todas las URLs del m3u8 para que pasen por /proxy."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{prox(base, m.group(1))}"', line))
        else:
            out.append(prox(base, s))
    return "\n".join(out) + "\n"

# ---------------------------------------------------------------- segmentos

def is_ts(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".ts")


def check(url: str, body: bytes):
    """Devuelve (body_ok, motivo). Playlists y llaves pasan tal cual.

    Para .ts se validan VARIOS sync-bytes (0x47), no solo el primero: un
    segmento puede empezar con 0x47 y estar corrupto por dentro, y eso es
    justo lo que provoca fragParsingError en hls.js."""
    if body[:64].lstrip()[:7] == b"#EXTM3U":
        return body, None
    if not body:
        return None, "respuesta vacía"
    if body[:1] == b"<":
        return None, "HTML en vez de video"
    if is_ts(url):
        if len(body) < 188 or body[0] != 0x47:
            return None, "no es MPEG-TS válido"
        n = len(body) // 188
        # sync-byte cada 188 bytes en hasta 8 paquetes (o todos si son pocos)
        for i in range(min(n, 8)):
            if body[i * 188] != 0x47:
                return None, "paquetes MPEG-TS corruptos"
        return body[: n * 188], None  # descarta paquete final cortado
    return body, None


async def fetch_checked(url: str):
    """GET completo con validación y reintentos -> (resp, body, error, intentos)."""
    reason = "?"
    for i in range(RETRIES):
        try:
            r = await http.get(url)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            reason = type(e).__name__
        else:
            if r.status_code < 400:
                body, why = check(url, r.content)
                if body is not None:
                    return r, body, None, i + 1
                reason = why
            else:
                reason = f"HTTP {r.status_code}"
                if r.status_code in (401, 403, 404, 410):
                    return None, None, reason, i + 1
        if i < RETRIES - 1:
            await asyncio.sleep(0.3 * (i + 1))
    return None, None, reason, RETRIES


async def passthrough(url: str, range_header: str):
    try:
        r = await http.send(http.build_request("GET", url, headers={"Range": range_header}), stream=True)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        return Response(f"upstream error: {e}", status_code=502)
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-range", "accept-ranges")}
    return StreamingResponse(r.aiter_bytes(), status_code=r.status_code, headers=keep,
                             background=BackgroundTask(r.aclose))


# ---------------------------------------------------------------- remux (ffmpeg)

def rx_stop():
    with rx_lock:
        p = rx["proc"]
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(2)
            except subprocess.TimeoutExpired:
                p.kill()
        if rx["log"]:
            rx["log"].close()
        rx["proc"] = rx["log"] = None


atexit.register(rx_stop)  # que ffmpeg no quede huérfano al cerrar el servidor


def rx_start(url: str, mode: str, relaunches: int = 0):
    with rx_lock:
        rx_stop()
        shutil.rmtree(RX_DIR, ignore_errors=True)
        RX_DIR.mkdir(parents=True, exist_ok=True)
        video = (["-c:v", "copy"] if mode == "remux" else
                 ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                  "-g", "60", "-keyint_min", "60", "-sc_threshold", "0"])
        # -igndts ignora DTS corruptos y -err_detect ignore_err descarta paquetes
        # malos: ambos evitan que timestamps destrozados del origen aborten ffmpeg.
        # -avoid_negative_ts / -max_muxing_queue_size normalizan el arranque para
        # que el primer segmento no genere bufferSeekOverHole en hls.js.
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
               "-protocol_whitelist", "http,https,tcp,tls,crypto",
               "-live_start_index", "-1",
               "-analyzeduration", "10000000", "-probesize", "10000000",
               "-fflags", "+genpts+discardcorrupt+igndts",
               "-err_detect", "ignore_err",
               "-user_agent", UA,
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
               "-i", url,
               "-map", "0:v:0?", "-map", "0:a:0?", *video,
               "-c:a", "aac", "-b:a", "128k", "-ac", "2",
               "-af", "aresample=async=1:min_hard_comp=0.1:first_pts=0",
               "-avoid_negative_ts", "make_zero",
               "-max_muxing_queue_size", "9999",
               "-f", "hls", "-hls_time", "4", "-hls_list_size", "8",
               "-hls_flags", "delete_segments+omit_endlist+independent_segments",
               "-hls_segment_filename", str(RX_DIR / "seg_%05d.ts"), str(RX_DIR / "index.m3u8")]
        lg = open(RX_DIR / "ffmpeg.log", "wb")
        flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # sin ventana de consola
        rx.update(proc=subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=lg, **flags),
                  log=lg, url=url, mode=mode, last=time.time(), started=time.time(), relaunches=relaunches,
                  crashes=0, crash_window=time.time(), dead=False)
        log(f"remux: ffmpeg iniciado ({mode})" + (f" [relanzamiento {relaunches}]" if relaunches else ""))


def rx_relaunch_if_needed():
    """ffmpeg descarta pistas que aparecen después del sondeo inicial (típico: el audio llega tarde).
    Si lo detecta en su log, se relanza: la nueva pasada arranca en el último segmento y ya ve la pista."""
    with rx_lock:
        p = rx["proc"]
        if not (p and p.poll() is None and rx["relaunches"] < 3 and time.time() - rx["started"] > 2):
            return False
        try:
            tail = (RX_DIR / "ffmpeg.log").read_text(errors="replace")[-4000:]
        except OSError:
            return False
        if re.search(r"New (audio|video) stream", tail):
            log("remux: apareció una pista nueva a mitad del stream, relanzando ffmpeg")
            rx_start(rx["url"], rx["mode"], rx["relaunches"] + 1)
            return True
        return False


def rx_note_crash() -> bool:
    """Cuenta caídas en ventana de 60 s. Devuelve True si se superó MAX_CRASHES."""
    now = time.time()
    if now - rx["crash_window"] > 60:
        rx["crash_window"] = now
        rx["crashes"] = 0
    rx["crashes"] += 1
    return rx["crashes"] > MAX_CRASHES


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(_):
    async def reaper():
        while True:
            await asyncio.sleep(5)
            p, now = rx["proc"], time.time()
            if not p:
                continue
            if p.poll() is None and now - rx["last"] > 60:
                log("remux: sin uso 60s, deteniendo ffmpeg")
                await asyncio.to_thread(rx_stop)
            elif p.poll() is None:
                await asyncio.to_thread(rx_relaunch_if_needed)
            elif not rx["dead"] and now - rx["last"] <= 60 and now - rx["started"] > 20:
                # ffmpeg se cayó. Anti-crash-loop: si se cae demasiadas veces
                # seguidas el canal está inservible; se detiene en vez de loopear.
                if rx_note_crash():
                    rx["dead"] = True
                    log(f"remux: ffmpeg cayó {rx['crashes']} veces seguidas; canal marcado inservible")
                    await asyncio.to_thread(rx_stop)
                else:
                    log(f"remux: ffmpeg se cayó ({rx['crashes']}), reiniciando")
                    await asyncio.to_thread(rx_start, rx["url"], rx["mode"])

    task = asyncio.create_task(reaper())
    yield
    task.cancel()
    rx_stop()
    await http.aclose()  # cierra el cliente httpx al apagar el servidor


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Solo acepta la página propia: bloquea otros sitios web y DNS rebinding."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if (host not in ALLOWED_HOSTS
            or request.headers.get("sec-fetch-site") == "cross-site"
            or (origin and origin != f"http://{host}")):
        return Response("forbidden", status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(Path(__file__).with_name("player.html"), headers=NOCACHE)


@app.get("/stats")
def get_stats():
    return {**stats, "cached_segments": len(segments), "recent_failures": list(recent_failures)}


@app.get("/proxy")
async def proxy(url: str, request: Request):
    # Validación de esquema: evita usar el proxy como SSRF local.
    if not re.match(r"https?://", url):
        return Response("url inválida", status_code=400)

    if "range" in request.headers:
        return await passthrough(url, request.headers["range"])

    name = urlparse(url).path.rsplit("/", 1)[-1] or url
    if url in segments:
        segments.move_to_end(url)
        stats["cache_hits"] += 1
        body, ctype = segments[url]
        return Response(body, media_type=ctype)

    r, body, err, tries = await fetch_checked(url)
    stats["retried"] += tries - 1
    if err:
        fail(f"{name}: {err} ({tries} intento/s)")
        stale = playlists.get(url)
        if stale and time.time() - stale[0] < 30:
            stats["stale_playlists"] += 1
            return Response(stale[1], media_type=M3U8, headers=NOCACHE)
        return Response(f"upstream: {err}", status_code=502)
    if tries > 1:
        log(f"retry ok {name} (intento {tries})")

    if body[:64].lstrip()[:7] == b"#EXTM3U":
        text = rewrite(body.decode("utf-8", "replace"), str(r.url))
        lru_put(playlists, url, (time.time(), text), 20)
        return Response(text, media_type=M3U8, headers=NOCACHE)

    ctype = "video/mp2t" if is_ts(url) else r.headers.get("content-type", "application/octet-stream")
    lru_put(segments, url, (body, ctype), 16)
    stats["ok"] += 1
    return Response(body, media_type=ctype)


@app.get("/remux/status")
def rx_status():
    rx_relaunch_if_needed()
    p = rx["proc"]
    running = bool(p and p.poll() is None)
    segs = len(list(RX_DIR.glob("seg_*.ts"))) if RX_DIR.exists() else 0
    if running:
        rx["last"] = time.time()
    tail = ""
    lg = RX_DIR / "ffmpeg.log"
    if lg.exists():
        tail = lg.read_text(errors="replace")[-600:]
    return {"available": bool(FFMPEG), "running": running, "segments": segs,
            "ready": running and segs >= 3, "mode": rx["mode"], "dead": rx["dead"], "log": tail}


@app.get("/remux/start")
def rx_start_ep(url: str, mode: str = "remux", force: int = 0):
    if not FFMPEG:
        return JSONResponse({"error": "ffmpeg no instalado"}, status_code=400)
    if not re.match(r"https?://", url) or mode not in ("remux", "transcode"):
        return JSONResponse({"error": "parámetros inválidos"}, status_code=400)
    with rx_lock:
        p = rx["proc"]
        same = bool(p and p.poll() is None and rx["url"] == url and rx["mode"] == mode and not rx["dead"])
        if force or not same:
            rx_start(url, mode)
        rx["last"] = time.time()
    return {"ok": True}


@app.get("/remux/{name}")
def rx_file(name: str):
    rx["last"] = time.time()
    if not re.fullmatch(r"index\.m3u8|seg_\d+\.ts", name):
        return Response(status_code=404)
    try:
        data = (RX_DIR / name).read_bytes()
    except OSError:
        return Response(status_code=404)
    return Response(data, media_type=M3U8 if name.endswith("m3u8") else "video/mp2t", headers=NOCACHE)


if __name__ == "__main__":
    print(f"Abre http://{HOST}:{PORT}   |   ffmpeg: {FFMPEG or 'NO encontrado (modos Remux/Transcode desactivados)'}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

