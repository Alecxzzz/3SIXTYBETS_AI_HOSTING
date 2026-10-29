# -*- coding: utf-8 -*-
"""
Diagnostico de canales del frontend (`channels.js`): que necesita PROXY y que
necesita TRANSCODIFICADOR.

Por que este script:
- El reproductor de TV vive en https (Vercel/Northflank). Un stream http:// en
  una pagina https lo bloquea el navegador (mixed content) y ademas hls.js
  necesita CORS en el m3u8 y en los segmentos. Todo eso lo resuelve /hls-proxy
  (mismo origen). No es una preferencia: si la fuente no manda
  Access-Control-Allow-Origin, SIN proxy no reproduce.
- Si el codec no es H.264/AAC (HEVC/H.265, AC-3/E-AC-3, MPEG-2) el navegador no
  lo decodifica: ahi entra el transcodificador FFmpeg (/live/index.m3u8).

Que hace:
- Lee channels.js y sondea CADA url (stream principal + alternatives).
- Del sondeo saca: status, content-type, si manda CORS, si es master/media,
  codecs (del master o SNIFFEANDO el primer segmento: stream_type del PMT en
  MPEG-TS o fourcc en fMP4), extension de segmento y resolucion.
- Clasifica cada URL en: OK_DIRECTO / NECESITA_PROXY / NECESITA_TRANSCODE /
  CAIDO, y resume por canal.

Uso:
    python diagnostico_canales.py                 # sondea y muestra el informe
    python diagnostico_canales.py --sin-sondeo    # solo estructura (sin red)
    python diagnostico_canales.py --json          # ademas guarda el JSON
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests


CHANNELS_JS = "channels.js"
REPORTE = "diagnostico_canales.json"
ORIGIN = "https://threesixtybets-chat.vercel.app"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = (6, 12)
MAX_HILOS = 20

# stream_type del PMT (MPEG-TS) -> codec
TS_TIPOS = {
    0x01: "MPEG-1 video", 0x02: "MPEG-2 video", 0x03: "MP3", 0x04: "MP3",
    0x0F: "AAC", 0x11: "AAC LATM", 0x1B: "H.264", 0x24: "HEVC/H.265",
    0x81: "AC-3", 0x87: "E-AC-3", 0x06: "privado/PCM",
}
VIDEO_OK = {"H.264"}
AUDIO_OK = {"AAC", "MP3"}
CAMPOS = ("name", "stream", "type", "geoRestriction", "useProxy", "logo")


def parse_channels(path=CHANNELS_JS):
    """Lee channels.js con regex (el formato es uniforme, 1 canal por linea)."""
    canales = []
    with open(path, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea.startswith("{id:"):
                continue
            canal = {}
            for campo in CAMPOS:
                if campo == "useProxy":
                    m = re.search(r"useProxy:\s*(true|false)", linea)
                    canal[campo] = bool(m and m.group(1) == "true")
                else:
                    m = re.search(campo + r':\s*"(.*?)"', linea)
                    canal[campo] = m.group(1) if m else ""
            m = re.search(r"alternatives:\s*\[(.*?)\]", linea)
            canal["alternatives"] = re.findall(r'"(.*?)"', m.group(1)) if m else []
            canales.append(canal)
    return canales


def _headers():
    # Origin simulado: si la fuente responde con Access-Control-Allow-Origin,
    # hls.js puede pedirla directo sin pasar por /hls-proxy.
    return {"User-Agent": UA, "Origin": ORIGIN, "Accept": "*/*"}


def _ts_stream_types(data):
    """stream_type declarados en el PMT de un MPEG-TS (paquetes de 188 bytes)."""
    pmt_pids = set()
    for i in range(0, len(data) - 187, 188):
        pkt = data[i:i + 188]
        if pkt[0] != 0x47 or not (pkt[1] & 0x40):
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != 0:  # solo PAT
            continue
        off = 5 + pkt[4]
        if off + 12 > 188 or pkt[off] != 0x00:
            continue
        for j in range(off + 8, 184, 4):
            prog = (pkt[j] << 8) | pkt[j + 1]
            pmt_pid = ((pkt[j + 2] & 0x1F) << 8) | pkt[j + 3]
            if prog:
                pmt_pids.add(pmt_pid)

    tipos = set()
    for i in range(0, len(data) - 187, 188):
        pkt = data[i:i + 188]
        if pkt[0] != 0x47 or not (pkt[1] & 0x40):
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid not in pmt_pids:
            continue
        off = 5 + pkt[4]
        if off + 12 > 188 or pkt[off] != 0x02:
            continue
        prog_info_len = ((pkt[off + 10] & 0x0F) << 8) | pkt[off + 11]
        j = off + 12 + prog_info_len
        while j + 5 < 184:
            tipos.add(pkt[j])
            j += 5 + (((pkt[j + 2] & 0x0F) << 8) | pkt[j + 3])
    return tipos


def _sniff_segmento(seg_url):
    """Descarga el inicio del segmento y deduce los codecs reales."""
    try:
        r = requests.get(seg_url, headers=_headers(), timeout=TIMEOUT, stream=True)
        datos = r.raw.read(200_000, decode_content=True) or b""
        r.close()
    except Exception:
        return "", ""

    if datos[:1] == b"\x47":  # MPEG-TS -> PMT
        tipos = {TS_TIPOS.get(t, f"tipo0x{t:02x}") for t in _ts_stream_types(datos)}
        return ", ".join(sorted(tipos)), "mpeg-ts"
    if b"ftyp" in datos[:64]:  # fMP4 -> fourcc
        vistos = [c for c in ("avc1", "hvc1", "hev1", "mp4a", "ac-3", "ec-3", "dvh1")
                  if c.encode() in datos[:200_000]]
        return ", ".join(vistos), "fmp4"
    if datos[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "AAC", "adts (solo audio)"
    return "", "desconocido"


def _analizar_manifest(texto, url):
    """Del texto de un m3u8 saca (tipo, codecs, resolucion, ext. segmento, 1er variant)."""
    kind = "master" if "#EXT-X-STREAM-INF" in texto else "media"
    codecs, res, variant = "", "", ""
    for m in re.finditer(r"#EXT-X-STREAM-INF:([^\n]+)\n([^\n#]+)", texto):
        attrs, siguiente = m.group(1), m.group(2).strip()
        if "CODECS" in attrs:
            codecs = (re.search(r'CODECS="([^"]+)"', attrs) or [None, codecs])[1]
        if "RESOLUTION" in attrs:
            res = (re.search(r"RESOLUTION=([0-9x]+)", attrs) or [None, res])[1]
        if not variant and siguiente:
            variant = requests.compat.urljoin(url, siguiente)
        if codecs:
            break
    ext = ""
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#"):
            ext = linea.split("?")[0].rsplit(".", 1)[-1].lower()
            break
    return kind, codecs, res, ext, variant


def probe(url):
    """Sondea una URL y devuelve el diagnostico de ESA fuente."""
    info = {
        "url": url, "scheme": url.split(":", 1)[0], "status": None, "ctype": "",
        "cors": None, "cors_ok": False, "kind": "", "codecs": "", "res": "",
        "ext": "", "nota": "", "necesita_proxy": False, "necesita_transcode": False,
    }
    if not url.lower().startswith(("http://", "https://")):
        info.update(status="-", nota="no http(s): iframe/embed", necesita_proxy=True)
        return info

    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT, stream=True)
        info["status"] = r.status_code
        info["ctype"] = (r.headers.get("content-type") or "").split(";")[0].strip()
        info["cors"] = r.headers.get("access-control-allow-origin")
        es_manifest = (url.split("?")[0].endswith(".m3u8")
                       or "mpegurl" in info["ctype"].lower())
        texto = r.text[:60_000] if (es_manifest and r.status_code == 200) else ""
        r.close()
    except Exception as exc:  # timeout, DNS, TLS...
        info["nota"] = type(exc).__name__
        return info

    if info["status"] != 200:
        info["nota"] = f"HTTP {info['status']}"
        return info

    if not texto:
        info["nota"] = f"no es HLS ({info['ctype'] or 'sin content-type'})"
        info["necesita_proxy"] = True
        info["necesita_transcode"] = True
        return info

    kind, codecs, res, ext, variant = _analizar_manifest(texto, url)
    info.update(kind=kind, codecs=codecs, res=res, ext=ext)

    # Master sin CODECS: mirar el primer variant y, si hace falta, su segmento.
    base, sub_texto = url, ""
    if kind == "master" and variant:
        base = variant
        try:
            r2 = requests.get(variant, headers=_headers(), timeout=TIMEOUT, stream=True)
            sub_texto = r2.text[:60_000] if r2.status_code == 200 else ""
            r2.close()
        except Exception:
            sub_texto = ""
    elif kind == "media":
        sub_texto = texto

    if not codecs and sub_texto:
        _, codecs, res2, ext2, _ = _analizar_manifest(sub_texto, base)
        info.update(codecs=codecs, ext=info["ext"] or ext2, res=info["res"] or res2)
    if not info["codecs"] and sub_texto:
        seg = next((requests.compat.urljoin(base, l.strip())
                    for l in sub_texto.splitlines()
                    if l.strip() and not l.startswith("#")), "")
        if seg:
            sniff, cont = _sniff_segmento(seg)
            info["codecs"] = sniff
            info["ext"] = info["ext"] or cont

    # --- Veredicto ---
    # Proxy si es http sobre pagina https (mixed content) o si la fuente no
    # autoriza NUESTRO origen por CORS: hls.js pide el m3u8 y los segmentos con
    # fetch, asi que un ACAO distinto (p.ej. el del propio proveedor) no sirve.
    cors = (info["cors"] or "").strip()
    info["cors_ok"] = cors == "*" or ORIGIN in cors
    info["necesita_proxy"] = info["scheme"] == "http" or not info["cors_ok"]
    cs = info["codecs"].upper()
    if cs:
        malos = [c for c in ("HEVC", "H.265", "HVC1", "HEV1", "MPEG-2", "MPEG-1",
                             "AC-3", "E-AC-3", "PRIVADO") if c in cs]
        info["necesita_transcode"] = bool(malos)
    return info


def veredicto(info):
    if info["status"] not in (200, "-"):
        return "CAIDO"
    if info["necesita_transcode"]:
        return "TRANSCODE"
    if info["necesita_proxy"]:
        return "PROXY"
    return "DIRECTO"


REPORTE_TXT = "diagnostico_canales.txt"
_SALIDA = []


def _p(texto=""):
    """Imprime y acumula el informe (asi el .txt sale en UTF-8, sin pasar por
    el redirect de PowerShell, que corrompe los acentos)."""
    print(texto)
    _SALIDA.append(texto)


def main() -> int:
    try:  # consola de Windows: evita UnicodeEncodeError con nombres tipo "Ñ"
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sin_sondeo = "--sin-sondeo" in sys.argv
    guardar = "--json" in sys.argv

    canales = parse_channels()
    tareas = []  # (canal, etiqueta, url)
    for canal in canales:
        tareas.append((canal, "principal", canal["stream"]))
        for i, alt in enumerate(canal["alternatives"], start=2):
            tareas.append((canal, f"opcion {i}", alt))

    guardar_txt = "--txt" in sys.argv
    _p(f"channels.js -> {len(canales)} canales, {len(tareas)} fuentes "
       f"(principal + opciones)")
    _p(f"Origen asumido del frontend (CORS): {ORIGIN}")
    _p()

    if sin_sondeo:
        for canal in canales:
            print(f"  {canal['name']:<34} proxy={str(canal['useProxy']):<5} "
                  f"opciones={len(canal['alternatives'])}  geo={canal['geoRestriction']}")
        return 0

    urls = sorted({u for _, _, u in tareas})
    print(f"Sondeando {len(urls)} URLs unicas con {MAX_HILOS} hilos...")
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as ex:
        resultados = dict(zip(urls, ex.map(probe, urls)))
    print(f"Listo en {time.time() - inicio:.0f}s\n")

    filas = []  # (canal, etiqueta, info, veredicto)
    for canal, etiqueta, url in tareas:
        info = resultados[url]
        filas.append((canal, etiqueta, info, veredicto(info)))

    _p("=" * 112)
    _p("INFORME POR CANAL")
    _p("DIRECTO   = se reproduce tal cual (https + CORS valido)")
    _p("PROXY     = tiene que pasar por /hls-proxy (http o CORS no valido)")
    _p("TRANSCODE = codec que el navegador no decodifica -> /live/index.m3u8 (FFmpeg)")
    _p("CAIDO     = la fuente no responde (403/404/timeout)")
    _p("=" * 112)
    for canal in canales:
        suyas = [f for f in filas if f[0] is canal]
        cabecera = "/".join(sorted({v for _, _, _, v in suyas}))
        _p(f"\n[{cabecera}] {canal['name']}   "
           f"(useProxy en channels.js: {str(canal['useProxy']).lower()})")
        for _, etiqueta, info, v in suyas:
            cors_txt = "cors=OK" if info["cors_ok"] else "cors=NO"
            if info["cors"] and not info["cors_ok"]:
                cors_txt += f"({info['cors']})"
            extra = " ".join(x for x in (
                f"HTTP {info['status']}" if info["status"] != 200 else "",
                info["codecs"], info["res"], f".{info['ext']}" if info["ext"] else "",
                cors_txt, info["nota"],
            ) if x)
            _p(f"    {etiqueta:<10} {v:<10} {info['scheme']:<5} {extra}")

    # --- Resumen ---
    _p("\n" + "=" * 112)
    _p("RESUMEN")
    _p("=" * 112)
    por_veredicto = {}
    for _, _, _, v in filas:
        por_veredicto[v] = por_veredicto.get(v, 0) + 1
    for v in ("DIRECTO", "PROXY", "TRANSCODE", "CAIDO"):
        _p(f"  {v:<10} {por_veredicto.get(v, 0)} fuente(s)")

    transcode = [f"{c['name']} [{e}] -> {i['codecs']}"
                 for c, e, i, v in filas if v == "TRANSCODE"]
    caidos = sorted({f"{c['name']} [{e}] {i['nota']}"
                     for c, e, i, v in filas if v == "CAIDO"})
    directo = sorted({c["name"] for c in canales
                      if all(v == "DIRECTO" for cc, _, _, v in filas if cc is c)})
    flag_mal = sorted({c["name"] for c in canales if not c["useProxy"]
                       and any(v in ("PROXY", "TRANSCODE") for cc, _, _, v in filas
                               if cc is c)})
    flag_ok = sorted({c["name"] for c in canales if c["useProxy"]
                      and all(v == "DIRECTO" for cc, _, _, v in filas if cc is c)})

    def _lista(titulo, items, vacio="ninguno"):
        _p(f"\n  {titulo}:")
        if not items:
            _p(f"    - {vacio}")
        for x in items:
            _p(f"    - {x}")

    _lista("A TRANSCODIFICAR (codec que el navegador no decodifica)", transcode)
    _lista("CAIDOS ahora mismo (fuente muerta: 403/404/timeout)", caidos)
    _lista("REPRODUCEN DIRECTO (https + CORS valido, sin proxy)", directo)
    _lista("useProxy=false PERO necesitan proxy/transcode (dato mal en channels.js)",
           flag_mal)
    _lista("useProxy=true aunque reproducen directo (proxy innecesario)", flag_ok)

    hosts = {}
    for url in urls:
        host = url.split("/")[2] if "://" in url else url
        ok = resultados[url]["status"] == 200
        hosts.setdefault(host, [0, 0])
        hosts[host][0] += 1
        hosts[host][1] += 1 if ok else 0
    _p("\n  FUENTES POR HOST (responden / total):")
    for host, (total, ok) in sorted(hosts.items(), key=lambda kv: -kv[1][0]):
        _p(f"    {host:<42} {ok}/{total}")

    if guardar:
        with open(REPORTE, "w", encoding="utf-8") as fh:
            json.dump(
                [{"canal": c["name"], "etiqueta": e, "veredicto": v, **i}
                 for c, e, i, v in filas],
                fh, ensure_ascii=False, indent=2,
            )
        _p(f"\n  JSON completo en {REPORTE}")
    if guardar_txt:
        with open(REPORTE_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_SALIDA) + "\n")
        print(f"  Informe de texto en {REPORTE_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
