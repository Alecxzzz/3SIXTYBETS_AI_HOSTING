"""
Transcodificador HLS on-demand.

Convierte cualquier fuente (HEVC, H.264 con timestamps rotos, etc.) a un
HLS universal H.264 + AAC que reproduce cualquier navegador. Un proceso
FFmpeg por canal visto; se apaga solo tras ~60s sin espectadores.

Uso desde main.py:
    key = transcoder.start_session(url, referer)
    playlist = transcoder.get_playlist(key)   # bloquea hasta que haya datos
    segmentos en transcoder.session_dir(key)
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time

SESSION_TTL = 75          # segundos sin peticiones antes de apagar ffmpeg
START_TIMEOUT = 30        # segundos max esperando la primera playlist
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_sessions = {}
_lock = threading.Lock()
_janitor_started = False


def _session_key(url: str, referer: str | None) -> str:
    raw = f"{url}|{referer or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _session_dir(key: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"live_{key}")


def _janitor():
    """Apaga sesiones sin actividad y limpia sus archivos."""
    while True:
        time.sleep(15)
        now = time.time()
        with _lock:
            dead = [
                k for k, s in _sessions.items()
                if now - s["last_access"] > SESSION_TTL
            ]
            for k in dead:
                sess = _sessions.pop(k)
                try:
                    sess["proc"].terminate()
                except Exception:
                    pass
                shutil.rmtree(sess["dir"], ignore_errors=True)


def _ensure_janitor():
    global _janitor_started
    with _lock:
        if not _janitor_started:
            threading.Thread(target=_janitor, daemon=True).start()
            _janitor_started = True


def start_session(url: str, referer: str | None = None) -> str:
    """Inicia (o reutiliza) el transcodificador para una URL. Devuelve el key."""
    _ensure_janitor()
    key = _session_key(url, referer)
    now = time.time()
    with _lock:
        sess = _sessions.get(key)
        if sess and sess["proc"].poll() is None:
            sess["last_access"] = now
            return key
        # Sesion muerta o inexistente: limpiar y arrancar de nuevo
        if sess:
            try:
                sess["proc"].kill()
            except Exception:
                pass
            shutil.rmtree(sess["dir"], ignore_errors=True)
        out_dir = _session_dir(key)
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        _sessions[key] = {
            "proc": None, "dir": out_dir, "last_access": now, "url": url,
            "referer": referer,
        }
    _spawn(key)
    return key


def _spawn(key: str):
    """Lanza el proceso FFmpeg de la sesion."""
    with _lock:
        sess = _sessions[key]
        out_dir = sess["dir"]
        url, referer = sess["url"], sess["referer"]
        cmd = [
            FFMPEG_BIN,
            "-hide_banner", "-loglevel", "error", "-nostdin",
            # Reparar timestamps del origen (discontinuities, gaps, pts raros)
            "-fflags", "+genpts+discardcorrupt+igndts",
            "-user_agent", USER_AGENT,
        ]
        if referer:
            cmd += ["-headers", f"Referer: {referer}\r\n"]
        cmd += [
            "-i", url,
            "-map", "0:v:0", "-map", "0:a:0?",
            # Video: H.264 universal
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-maxrate", "2500k", "-bufsize", "3500k",
            "-pix_fmt", "yuv420p",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            # Audio: AAC estereo
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            # HLS local: segmentos de 2s, ventana corta, auto-limpieza
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "6",
            "-hls_delete_threshold", "12",
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_filename", os.path.join(out_dir, "seg_%05d.ts"),
            os.path.join(out_dir, "index.m3u8"),
        ]
        try:
            sess["proc"] = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "FFmpeg no esta instalado en el servidor (instalar ffmpeg)."
            ) from exc


def touch(key: str):
    """Marca la sesion como activa (alguien la esta viendo)."""
    with _lock:
        sess = _sessions.get(key)
        if sess:
            sess["last_access"] = time.time()


def is_alive(key: str) -> bool:
    with _lock:
        sess = _sessions.get(key)
        return bool(sess and sess["proc"] and sess["proc"].poll() is None)


def get_playlist(key: str) -> str:
    """
    Espera (hasta START_TIMEOUT) a que exista la playlist con al menos un
    segmento y la devuelve con las URLs apuntando a /live/{key}/...
    Lanza RuntimeError si el proceso murio sin producir datos.
    """
    path = os.path.join(_session_dir(key), "index.m3u8")
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if "#EXTINF" in text:
                lines = []
                for line in text.splitlines():
                    if line.startswith("#") or not line.strip():
                        lines.append(line)
                    else:
                        lines.append(f"/live/{key}/{line.strip()}")
                touch(key)
                return "\n".join(lines) + "\n"
        if not is_alive(key):
            break
        time.sleep(0.5)
    raise RuntimeError("El transcodificador no pudo iniciar para esta fuente.")


def get_segment_path(key: str, name: str) -> str | None:
    """Ruta segura del segmento pedido (evita path traversal)."""
    if "/" in name or "\\" in name or ".." in name:
        return None
    if not (name.startswith("seg_") and name.endswith(".ts")):
        return None
    path = os.path.join(_session_dir(key), name)
    if not os.path.isfile(path):
        return None
    touch(key)
    return path

