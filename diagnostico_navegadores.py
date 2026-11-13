# -*- coding: utf-8 -*-
"""DIAGNOSTICO POR NAVEGADOR de los canales de `channels.js`.

Que responde, canal por canal:
    ¿se puede reproducir DIRECTO, necesita /hls-proxy, o necesita FFmpeg?
    ¿y en Safari, Chrome, Edge y Firefox?

Por que no basta con "proxy si / transcode si"
-----------------------------------------------
Un mismo canal se comporta distinto segun el navegador, por tres motivos que se
combinan:

1. MEZCLA DE CONTENIDO. La web es https. Un stream http:// lo bloquea TODO
   navegador. Solucion unica: pasar por /hls-proxy (que si es https).

2. CORS. hls.js pide el m3u8 y los segmentos con XHR: si la fuente no manda
   `Access-Control-Allow-Origin`, el navegador lo bloquea. Safari NO tiene este
   problema cuando reproduce HLS de forma nativa (no usa XHR). Chrome, Edge y
   Firefox SIEMPRE usan hls.js en escritorio (no tienen HLS nativo), asi que
   necesitan CORS o proxy.

3. CODEC. Aqui esta la diferencia grande:
      H.264 + AAC  -> todos los navegadores.
      HEVC/H.265   -> Safari si; Chrome/Edge solo con hardware y sistema que lo
                      soporte; Firefox no.
      AC-3 / E-AC-3-> Safari si (HLS nativo); Chrome/Edge/Firefox no.
      MPEG-2/1     -> nadie.
   Si el codec no es universal, la unica via que funciona en TODOS es FFmpeg
   (/live/index.m3u8), que reencoda a H.264/AAC.

Estrategia que sale de esto (la que conviene implementar)
---------------------------------------------------------
    Safari  -> casi siempre puede ir DIRECTO (nativo, sin CORS).
    Chrome/Edge/Firefox -> mismo camino entre ellos: proxy si falta CORS,
                           transcode si el codec no es H.264/AAC.
    Si se quiere UNA sola ruta para todos: la mas restrictiva (transcode).

Uso:
    python diagnostico_navegadores.py               # sondea y genera el informe
    python diagnostico_navegadores.py --rapido      # solo sondeo de cabeceras
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from diagnostico_canales import (MAX_HILOS, ORIGIN, parse_channels, probe,
                                 veredicto)

REPORTE_MD = "DIAGNOSTICO_CANALES_NAVEGADORES.md"
REPORTE_JSON = "diagnostico_navegadores.json"

NAVEGADORES = ("Safari", "Chrome", "Edge", "Firefox")

# Codigos que cada familia de navegador decodifica.
# Fuente: caniuse + notas de soporte de MSE por plataforma (2026).
SOPORTA = {
    "Safari":  {"H.264", "AAC", "MP3", "HEVC/H.265", "AC-3", "E-AC-3"},
    "Chrome":  {"H.264", "AAC", "MP3"},          # HEVC solo con HW: no se asume
    "Edge":    {"H.264", "AAC", "MP3"},          # idem (Chromium)
    "Firefox": {"H.264", "AAC", "MP3"},
}
# Safari reproduce HLS de forma nativa: no hace XHR, luego no necesita CORS.
CORS_NECESARIO = {"Safari": False, "Chrome": True, "Edge": True, "Firefox": True}


def _codecs_de(info) -> set:
    """Codecs detectados, normalizados a las claves de SOPORTA.

    OJO con las variantes: el sondeo puede devolver "H.264" (nombre del PMT),
    "avc1" (fourcc del fMP4), "H264" o "AVC". Buscar solo "H264" dejaba los
    canales H.264 como "codec desconocido" y los mandaba a transcodificar sin
    necesidad. Aqui se cubren todas las formas.
    """
    texto = (info.get("codecs") or "").upper()
    hallados = set()

    # Audio primero: "E-AC-3" contiene "AC-3", hay que distinguirlos.
    if "E-AC-3" in texto or "EC-3" in texto:
        hallados.add("E-AC-3")
    elif "AC-3" in texto:
        hallados.add("AC-3")
    if "MP4A" in texto or "AAC" in texto or "LATM" in texto:
        hallados.add("AAC")
    if "MP3" in texto:
        hallados.add("MP3")

    # Video
    if "HVC1" in texto or "HEV1" in texto or "HEVC" in texto or "H.265" in texto:
        hallados.add("HEVC/H.265")
    elif ("H.264" in texto or "H264" in texto or "AVC1" in texto
          or "AVC" in texto or "TYPE0X1B" in texto):
        hallados.add("H.264")
    if "MPEG-2" in texto or "MPEG2" in texto:
        hallados.add("MPEG-2")
    if "MPEG-1" in texto or "MPEG1" in texto:
        hallados.add("MPEG-1")

    # Cualquier cosa rara ("privado/PCM", "tipo0x81"...) cuenta como no soportada.
    if "PRIVADO" in texto or "PCM" in texto or "TIPO0X" in texto:
        hallados.add("DESCONOCIDO")
    return hallados


def ruta_por_navegador(info) -> dict:
    """Que ruta necesita CADA navegador para esta fuente.

    Devuelve {navegador: "directo" | "proxy" | "transcode" | "no_soportado"}.

    El orden de las comprobaciones es el de la gravedad:
      1. si la fuente no responde -> no_soportado en todos
      2. codec no decodificable -> transcode (solo asi funciona en todos)
      3. http:// en pagina https -> proxy (mezcla de contenido, todos)
      4. sin CORS y el navegador usa XHR -> proxy
      5. resto -> directo
    """
    salida = {}
    caido = info.get("status") not in (200, "-")
    if caido:
        return {n: "no_soportado" for n in NAVEGADORES}

    codecs = _codecs_de(info)
    # Si no se pudo detectar el codec, no se afirma nada: se asume lo peor.
    desconocido = not codecs
    http_plano = info.get("scheme") == "http"
    sin_cors = not info.get("cors_ok")

    for nav in NAVEGADORES:
        soportados = SOPORTA[nav]
        malo = bool(codecs - soportados) or desconocido
        if malo:
            salida[nav] = "transcode"
        elif http_plano:
            salida[nav] = "proxy"
        elif sin_cors and CORS_NECESARIO[nav]:
            salida[nav] = "proxy"
        else:
            salida[nav] = "directo"
    return salida


def ruta_unica(info) -> str:
    """La ruta que funciona en TODOS los navegadores (la mas restrictiva)."""
    rutas = set(ruta_por_navegador(info).values())
    for candidata in ("no_soportado", "transcode", "proxy", "directo"):
        if candidata in rutas:
            return candidata
    return "directo"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Modo sin red: recalcula el informe desde el JSON ya sondeado. Sirve para
    # corregir las reglas de clasificacion sin volver a golpear 96 URLs.
    if "--desde-json" in sys.argv and os.path.exists(REPORTE_JSON):
        with open(REPORTE_JSON, encoding="utf-8") as fh:
            datos = json.load(fh)
        filas = datos.get("filas") or []
        total = len({f["canal"] for f in filas})
        for f in filas:
            info = {"status": f.get("status"), "scheme": f.get("scheme"),
                    "codecs": f.get("codecs"), "cors_ok": f.get("cors_ok")}
            f["navegadores"] = ruta_por_navegador(info)
            f["ruta_unica"] = ruta_unica(info)
        datos["filas"] = filas
        with open(REPORTE_JSON, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=2, ensure_ascii=False)
        texto = informe_md(filas, total)
        with open(REPORTE_MD, "w", encoding="utf-8") as fh:
            fh.write(texto)
        print(f"Regenerado desde {REPORTE_JSON} (sin red): {total} canales.")
        print(f"Informe: {REPORTE_MD}")
        return 0

    canales = parse_channels()
    tareas = []
    for canal in canales:
        tareas.append((canal, "principal", canal["stream"]))
        for i, alt in enumerate(canal["alternatives"], start=2):
            tareas.append((canal, f"opcion {i}", alt))

    urls = sorted({u for _, _, u in tareas})
    print(f"channels.js -> {len(canales)} canales, {len(urls)} URLs unicas.")
    print(f"Sondeando con {MAX_HILOS} hilos (puede tardar 1-2 min)...", flush=True)
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=MAX_HILOS) as ex:
        resultados = dict(zip(urls, ex.map(probe, urls)))
    print(f"Listo en {time.time() - inicio:.0f}s\n", flush=True)

    filas = []
    for canal, etiqueta, url in tareas:
        info = resultados[url]
        filas.append({
            "canal": canal["name"],
            "etiqueta": etiqueta,
            "url": url,
            "useProxy_channelsjs": canal["useProxy"],
            "veredicto": veredicto(info),
            "status": info.get("status"),
            "status_http": info.get("status_http"),
            "scheme": info.get("scheme"),
            "codecs": info.get("codecs") or "",
            "res": info.get("res") or "",
            "ext": info.get("ext") or "",
            "cors": info.get("cors"),
            "cors_ok": bool(info.get("cors_ok")),
            "nota": info.get("nota") or "",
            "navegadores": ruta_por_navegador(info),
            "ruta_unica": ruta_unica(info),
        })

    with open(REPORTE_JSON, "w", encoding="utf-8") as fh:
        json.dump({"origen_cors": ORIGIN, "filas": filas}, fh, indent=2,
                  ensure_ascii=False)
    print(f"JSON: {REPORTE_JSON}")

    texto = informe_md(filas, len(canales))
    with open(REPORTE_MD, "w", encoding="utf-8") as fh:
        fh.write(texto)
    print(f"Informe: {REPORTE_MD}")
    print("\n" + texto[:3000])
    return 0


def _icono(ruta: str) -> str:
    return {
        "directo": "✅ directo",
        "proxy": "🔀 proxy",
        "transcode": "🎛️ transcode",
        "no_soportado": "❌ caído",
    }.get(ruta, ruta)


def _resumen_por_canal(filas):
    """Agrupa por canal y elige la MEJOR fuente (la que funciona en mas navegadores)."""
    orden = {"directo": 0, "proxy": 1, "transcode": 2, "no_soportado": 3}
    canales = {}
    for f in filas:
        actual = canales.get(f["canal"])
        if actual is None or orden[f["ruta_unica"]] < orden[actual["ruta_unica"]]:
            canales[f["canal"]] = f
    return canales


def informe_md(filas, total_canales: int) -> str:
    """Informe legible: metodologia, reglas por navegador y tabla por canal."""
    mejores = _resumen_por_canal(filas)
    cuenta = {"directo": 0, "proxy": 0, "transcode": 0, "no_soportado": 0}
    for f in mejores.values():
        cuenta[f["ruta_unica"]] = cuenta.get(f["ruta_unica"], 0) + 1

    l = []
    l.append("# Diagnóstico de canales por navegador\n")
    l.append(f"Fuente: `channels.js` — **{total_canales} canales**, "
             f"{len(filas)} fuentes (principal + alternativas). "
             f"Origen CORS simulado: `{ORIGIN}`.\n")

    l.append("## Reglas que se aplican\n")
    l.append("| Códec / condición | Safari | Chrome | Edge | Firefox |")
    l.append("|---|---|---|---|---|")
    for codec in ("H.264", "AAC", "MP3", "HEVC/H.265", "AC-3", "E-AC-3",
                  "MPEG-2", "MPEG-1"):
        fila = [f"**{codec}**"]
        for nav in NAVEGADORES:
            fila.append("✅" if codec in SOPORTA[nav] else "❌")
        l.append("| " + " | ".join(fila) + " |")
    l.append("| **Necesita CORS** | ❌ no (HLS nativo) | ✅ sí | ✅ sí | ✅ sí |")
    l.append("| **http:// en página https** | ❌ bloqueado | ❌ bloqueado | ❌ bloqueado | ❌ bloqueado |")
    l.append("")
    l.append("Notas que importan:\n")
    l.append("- **Safari** reproduce HLS de forma nativa: no usa XHR, por eso "
             "**no necesita CORS** y decodifica HEVC y AC-3.")
    l.append("- **Chrome/Edge/Firefox** en escritorio no tienen HLS nativo: usan "
             "hls.js (XHR), así que **necesitan CORS** y solo H.264+AAC de forma "
             "fiable. HEVC depende de hardware y sistema; no se asume.")
    l.append("- Si el códec no es universal, la única ruta que funciona en todos "
             "es **FFmpeg** (`/live/index.m3u8`).\n")

    l.append("## Resumen\n")
    l.append(f"- ✅ **Directo** (tal cual, sin tocar nada): **{cuenta['directo']}**")
    l.append(f"- 🔀 **Proxy** (`/hls-proxy`, por CORS o por http): **{cuenta['proxy']}**")
    l.append(f"- 🎛️ **Transcode** (`/live/index.m3u8`, por códec): **{cuenta['transcode']}**")
    l.append(f"- ❌ **Caídos** (ni principal ni alternativas): **{cuenta['no_soportado']}**\n")

    l.append("## Canal por canal\n")
    l.append("`ruta única` = la que funciona en los cuatro navegadores.\n")
    l.append("| Canal | Códec | http | CORS | Safari | Chrome | Edge | Firefox | Ruta única |")
    l.append("|---|---|---|---|---|---|---|---|---|")
    for nombre, f in sorted(mejores.items()):
        nav = f["navegadores"]
        l.append(
            f"| **{nombre}** | `{f['codecs'] or '?'}` | "
            f"{'⚠️ sí' if f['scheme'] == 'http' else 'https'} | "
            f"{'ok' if f['cors_ok'] else 'no'} | "
            f"{_icono(nav['Safari'])} | {_icono(nav['Chrome'])} | "
            f"{_icono(nav['Edge'])} | {_icono(nav['Firefox'])} | "
            f"**{f['ruta_unica']}** |"
        )

    l.append("\n## Qué hacer con cada grupo\n")
    por_ruta = {}
    for nombre, f in mejores.items():
        por_ruta.setdefault(f["ruta_unica"], []).append(nombre)

    if por_ruta.get("directo"):
        l.append(f"**✅ Directo ({len(por_ruta['directo'])})** — se reproducen sin "
                 f"proxy ni FFmpeg (https + CORS + H.264/AAC): "
                 f"{', '.join(sorted(por_ruta['directo']))}")
    if por_ruta.get("proxy"):
        l.append(f"\n**🔀 Proxy ({len(por_ruta['proxy'])})** — son http:// o no "
                 f"mandan CORS. Con `/hls-proxy` funcionan en los cuatro: "
                 f"{', '.join(sorted(por_ruta['proxy']))}")
    if por_ruta.get("transcode"):
        l.append(f"\n**🎛️ Transcode ({len(por_ruta['transcode'])})** — códec que "
                 f"el navegador no decodifica (HEVC/AC-3/MPEG o desconocido). "
                 f"Necesitan FFmpeg para verse en todos: "
                 f"{', '.join(sorted(por_ruta['transcode']))}")
    if por_ruta.get("no_soportado"):
        l.append(f"\n**❌ Caídos ({len(por_ruta['no_soportado'])})** — no "
                 f"responden ni el principal ni las alternativas: "
                 f"{', '.join(sorted(por_ruta['no_soportado']))}")

    l.append("\n## Cómo usarlo en el reproductor\n")
    l.append("```js")
    l.append("const RUTA = { directo: (u) => u,")
    l.append("               proxy: (u) => `/hls-proxy?url=${encodeURIComponent(u)}`,")
    l.append("               transcode: (u) => `/live/index.m3u8?url=${encodeURIComponent(u)}` };")
    l.append("// Safari puede ir directo aunque falte CORS; Chrome/Edge/Firefox no.")
    l.append("const esSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);")
    l.append("const ruta = esSafari && canal.navegadores.Safari !== 'transcode'")
    l.append("           ? canal.navegadores.Safari : canal.navegadores.Chrome;")
    l.append("hls.loadSource(RUTA[ruta](canal.stream));")
    l.append("```\n")
    return "\n".join(l) + "\n"


if __name__ == "__main__":
    sys.exit(main())


