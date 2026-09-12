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

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

# Token anti-abuso. CAMBIALO en produccion (env RELAY_TOKEN).
RELAY_TOKEN = os.getenv("RELAY_TOKEN", "3SIXTY-RELE-CAMBIA-ESTO")

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

    r = requests.get(u, headers=HDRS, timeout=TIMEOUT)
    if r.status_code != 200:
        raise HTTPException(502, f"upstream {r.status_code}")

    xml = r.text
    # Directorio del .mpd: ahi viven init y segmentos (rutas relativas)
    base_dir = u.rsplit("/", 1)[0] + "/"
    blob = base64.urlsafe_b64encode(base_dir.encode()).decode().rstrip("=")
    base_rele = f"/r/{k}/{blob}/"

    # Sustituir la BaseURL existente (si hay) o insertarla tras el tag raiz
    if "<BaseURL" in xml:
        xml = re.sub(
            r"<BaseURL[^>]*>.*?</BaseURL>",
            f"<BaseURL>{base_rele}</BaseURL>",
            xml,
            flags=re.DOTALL,
        )
    else:
        xml = re.sub(
            r"(<MPD\b[^>]*>)",
            r"\1<BaseURL>" + base_rele + "</BaseURL>",
            xml,
            count=1,
        )

    return Response(
        xml,
        media_type="application/dash+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/r/{k}/{blob}/{path:path}")
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

    r = requests.get(upstream, headers=HDRS, timeout=TIMEOUT, stream=True)
    if r.status_code != 200:
        return Response(f"upstream {r.status_code}", status_code=502)

    return StreamingResponse(
        r.iter_content(chunk_size=64 * 1024),
        media_type=r.headers.get("content-type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
