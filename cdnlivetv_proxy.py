"""
CDN Live TV - Proxy de resolucion dinamica
==========================================
Convierte los streams con token temporal de cdnlivetv.tv en enlaces "fijos"
locales. Tu app IPTV apunta a este servidor; el servidor resuelve el m3u8
fresco (token nuevo) en cada peticion y redirige.

Uso:
    python cdnlivetv_proxy.py [puerto]

Endpoints:
    /playlist.m3u                          -> lista IPTV con todos los canales
    /watch/<Nombre Canal>/<code>.m3u8      -> redirige al stream fresco (302)
    /health                                -> estado del servidor

Ejemplo en TiviMate/VLC:  http://localhost:8392/playlist.m3u
"""
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdnlivetv_resolve import resolve, check

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8392

# Canales expuestos en la lista M3U (nombre exacto en cdnlivetv.tv, codigo pais)
CHANNELS = [
    ("Sportsnet Ontario", "ca"),
    ("Sportsnet One", "ca"),
    ("Sportsnet 360", "ca"),
    ("Sportsnet East", "ca"),
    ("Sportsnet World", "ca"),
    ("Sportsnet West", "ca"),
    ("MLB Network", "us"),
]

_cache = {}  # (name, code) -> (url, generado_en)


def get_fresh(name, code):
    """Devuelve un m3u8 fresco; reusa cache por 60 min y revalida si falla.
    Reintenta hasta 3 veces con pausa (la API de cdnlivetv tiene rate-limit)."""
    key = (name, code)
    url, ts = _cache.get(key, (None, 0))
    if url and time.time() - ts < 3600:
        st, _ = check(url)
        if st == 200:
            return url
    for attempt in range(3):
        stream, _ = resolve(name, code)
        if stream:
            st, _ = check(stream)
            if st == 200:
                _cache[key] = (stream, time.time())
                return stream
        time.sleep(2 * (attempt + 1))
    return url  # ultimo recurso: el viejo aunque expire


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.unquote(self.path)
        if path == "/health":
            self._send(200, f"OK {time.strftime('%Y-%m-%d %H:%M:%S')}")
            return

        if path == "/playlist.m3u":
            lines = ["#EXTM3U"]
            for name, code in CHANNELS:
                lines.append(
                    f'#EXTINF:-1 tvg-id="{name}" group-title="Sports (CDN Live TV)",{name}'
                )
                host = self.headers.get("Host", f"localhost:{PORT}")
                lines.append(f"http://{host}/watch/{urllib.parse.quote(name)}/{code}.m3u8")
            self._send(200, "\n".join(lines), "audio/x-mpegurl")
            return

        m = re.match(r"^/watch/(.+)/(\w{2})\.m3u8$", path)
        if m:
            name, code = m.group(1), m.group(2)
            url = get_fresh(name, code)
            if url:
                self.send_response(302)
                self.send_header("Location", url)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(503, f"No se pudo resolver: {name} ({code})\n")
            return

        self._send(404, "Ruta desconocida. Usa /playlist.m3u\n")


if __name__ == "__main__":
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"CDN Live TV proxy escuchando en http://localhost:{PORT}")
    print(f"Lista IPTV: http://localhost:{PORT}/playlist.m3u")
    print("Canales:", ", ".join(n for n, c in CHANNELS))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Saliendo...")
