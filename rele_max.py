"""
Relé reverse-proxy para streams con geobloqueo (ej. Max/HBO).

Problema: el CDN de Max solo acepta IPs de ciertos paises
(PA, SV, HN, BZ, NI, DO, MX, GT, CR). El servidor de Northflank esta
en otra region y recibe 452, asi que FFmpeg no puede descargar el
manifiesto ni los segmentos.

Solucion: este relé corre en un VPS DENTRO de un pais permitido.
FFmpeg (backend) pide el manifiesto al relé; el relé lo descarga desde
su IP (aceptada), inserta un <BaseURL> que apunta de vuelta al relé,
y sirve los segmentos haciendo de intermediario. Todo sin estado
(stateless): la URL original del CDN viaja codificada en base64url
dentro de la propia ruta.

Rutas:
  /health
      Ping.
  /mpd/{token}?u=<url del .mpd original>
      Descarga el manifiesto DASH, sustituye/inserta <BaseURL> hacia
      este relé y lo devuelve. FFmpeg resuelve los segmentos relativos
      contra ese BaseURL automaticamente.
  /r/{token}/{base64url}/{ruta...}
      Sirve init/segmentos: upstream = base64url + ruta (+ query).

Seguridad: token OBLIGATORIO (RELAY_TOKEN) para que el relé no sea un
proxy abierto que cualquiera pueda abusar.

Despliegue (en el VPS del pais permitido):
  pip3 install fastapi uvicorn requests
  RELAY_TOKEN=tu_token_secreto uvicorn rele_max:app --host 0.0.0.0 --port 8000
  (abrir el puerto 8000 en el firewall)
"""

import base64
import os
import re
import time
from urllib.parse import urljoin

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

# Token anti-abuso. CAMBIALO en produccion (env RELAY_TOKEN).
RELAY_TOKEN = os.getenv("RELAY_TOKEN", "3SIXTY-RELE-CAMBIA-ESTO")

# URL publica de este rele (ej. el tunel de cloudflared o la IP del VPS).
# Necesaria para que las BaseURL del manifiesto sean ABSOLUTAS: el demuxer
# DASH de ffmpeg ignora BaseURL relativas.
RELAY_PUBLIC_URL = os.getenv("RELAY_PUBLIC_URL", "").rstrip("/")

ACCESOS_LOG = os.path.join(os.path.dirname(__file__), "_rele_accesos.log")


def _log(msg: str):
    try:
        with open(ACCESOS_LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%H:%M:%S] ") + msg + "\n")
    except OSError:
        pass

# El CDN exige un User-Agent de navegador real.
UPSTREAM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HDRS = {"User-Agent": UPSTREAM_UA, "Accept": "*/*"}
TIMEOUT = 20

app = FastAPI()


def _autorizado(k: str):
    if not RELAY_TOKEN or k != RELAY_TOKEN:
        raise HTTPException(403, "token invalido")


def _b64url_decode(blob: str) -> str:
    padding = "=" * (-len(blob) % 4)
    return base64.urlsafe_b64decode(blob + padding).decode("utf-8", "replace")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/mpd/{k}")
def manifiesto(k: str, u: str):
    """Devuelve el manifiesto DASH reescrito para que los segmentos
    relativos se resuelvan contra este relé."""
    _autorizado(k)
    if not u.startswith(("http://", "https://")):
        raise HTTPException(400, "url invalida")

    ultimo = None
    r = None
    for _intento in range(3):
        try:
            r = requests.get(u, headers=HDRS, timeout=TIMEOUT)
            if r.status_code == 200:
                break
            ultimo = f"upstream {r.status_code}"
        except requests.exceptions.RequestException as exc:
            ultimo = f"error de conexion: {exc}"
        r = None
        time.sleep(2)
    if r is None:
        raise HTTPException(502, ultimo or "upstream sin respuesta")

    xml = r.text
    base_dir = u.rsplit("/", 1)[0] + "/"

    def _b64(txt):
        return base64.urlsafe_b64encode(txt.encode()).decode().rstrip("=")

    def _destino(base):
        if not base.endswith("/"):
            base += "/"
        bb = _b64(base)
        if RELAY_PUBLIC_URL:
            return f"{RELAY_PUBLIC_URL}/r/{k}/{bb}/"
        return f"r/{k}/{bb}/"

    def _attr_factory(destino):
        def _attr(m2):
            actual = m2.group(2)
            if actual.startswith("http") or actual.startswith("/r/"):
                return m2.group(0)  # ya reescrita, no tocar
            return f'{m2.group(1)}="{destino}{actual}"'
        return _attr

    def _quitar_baseurls(bloque):
        return re.sub(r"<BaseURL[^>]*>[^<]*</BaseURL>", "", bloque)

    def _clonar_st_en_reps(bloque, st_xml, base_default):
        """Copia el SegmentTemplate (nivel AdaptationSet) dentro de cada
        Representation, reescrito con la base ABSOLUTA de cada una. Necesario
        porque el dashdec de ffmpeg viejo ignora los <BaseURL> anidados."""
        reps = list(re.finditer(r"<Representation\b.*?</Representation>", bloque, re.DOTALL))
        if not reps:
            return None
        salida, ultimo = [], 0
        for rep in reps:
            salida.append(bloque[ultimo:rep.start()])
            rb = rep.group(0)
            m_base = re.search(r"<BaseURL[^>]*>([^<]+)</BaseURL>", rb)
            base = m_base.group(1).strip() if m_base else base_default
            att = _attr_factory(_destino(base))
            st_copia = re.sub(r'(initialization)="([^"]+)"', att, st_xml)
            st_copia = re.sub(r'(\bmedia)="([^"]+)"', att, st_copia)
            rb = _quitar_baseurls(rb)
            rb = re.sub(r"(<Representation\b[^>]*>)", lambda mm: mm.group(1) + st_copia, rb, count=1)
            salida.append(rb)
            ultimo = rep.end()
        salida.append(bloque[ultimo:])
        return "".join(salida)

    def _procesar_set(m):
        bloque = m.group(0)
        m_st = re.search(r"<SegmentTemplate\b.*?</SegmentTemplate>", bloque, re.DOTALL)
        if not m_st:
            # Sin SegmentTemplate en el set
            return _quitar_baseurls(bloque)
        # Base del set: su propia BaseURL (los reps del set la comparten);
        # si no tiene, el directorio del manifiesto.
        b = re.search(r"<BaseURL[^>]*>([^<]+)</BaseURL>", bloque)
        base = b.group(1).strip() if b else base_dir
        att = _attr_factory(_destino(base))
        # Reescribir el ST UNA vez (donde este: nivel set o dentro del set).
        # FFmpeg viejo ignora <BaseURL> anidados, por eso va en la plantilla.
        bloque = re.sub(r'(initialization)="([^"]+)"', att, bloque)
        bloque = re.sub(r'(\bmedia)="([^"]+)"', att, bloque)
        return _quitar_baseurls(bloque)

    xml = re.sub(r"<AdaptationSet\b.*?</AdaptationSet>", _procesar_set, xml, flags=re.DOTALL)

    def _procesar_rep(m):
        # Representations con SegmentTemplate propio y BaseURL heredada
        bloque = m.group(0)
        if "<SegmentTemplate" not in bloque:
            return bloque
        m_base = re.search(r"<BaseURL[^>]*>([^<]+)</BaseURL>", bloque)
        base = m_base.group(1).strip() if m_base else base_dir
        att = _attr_factory(_destino(base))
        bloque = re.sub(r'(initialization)="([^"]+)"', att, bloque)
        bloque = re.sub(r'(\bmedia)="([^"]+)"', att, bloque)
        return _quitar_baseurls(bloque)

    xml = re.sub(r"<Representation\b.*?</Representation>", _procesar_rep, xml, flags=re.DOTALL)
    # BaseURLs restantes (nivel MPD): fuera
    xml = re.sub(r"<BaseURL[^>]*>[^<]*</BaseURL>", "", xml)

    _log(f"/mpd servido: {len(xml)} bytes, BaseURL reescritas ok, init/media absolutas al rele")
    return Response(
        xml,
        media_type="application/dash+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/r/{k}/{blob}/{path:path}")
@app.get("/mpd/{k}/r/{blob}/{path:path}")
def segmento(k: str, blob: str, path: str, request: Request):
    """Sirve init/segmentos: upstream = base64url + ruta (stateless)."""
    _autorizado(k)
    try:
        base = _b64url_decode(blob)
    except Exception:
        raise HTTPException(400, "base invalida")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(400, "base invalida")

    upstream = base + path
    if request.url.query:
        upstream += "?" + request.url.query

    _log(f"seg pedido: base={base[:70]} path={path[:70]}")

    r = None
    for _intento in range(2):
        try:
            r = requests.get(upstream, headers=HDRS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                break
            print(f"[rele] segmento upstream {r.status_code}: {upstream[:100]}...")
            _log(f"seg upstream {r.status_code}: {upstream[:140]}")
        except requests.exceptions.RequestException as exc:
            print(f"[rele] segmento error: {exc}")
        r = None
        time.sleep(1)
    if r is None:
        return Response("upstream sin respuesta", status_code=502)

    return StreamingResponse(
        r.iter_content(chunk_size=64 * 1024),
        media_type=r.headers.get("content-type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
