"""
Cruza la agenda de HOY de dlive.sx con el catalogo de cdnlivetv.tv
=================================================================
dlive.sx publica el canal REAL de cada evento (misma logica que
main.dlive_agenda()); cdnlivetv.tv es el CDN del que sacamos los m3u8.
Este script dice, evento por evento, que canal de esos se puede sacar del
CDN y si esta online ahora mismo.

Uso:
    python analizar_agenda_cdn.py              # resumen por liga + detalle en agenda_hoy_cdn.txt
    python analizar_agenda_cdn.py --detalle    # imprime tambien el detalle completo
"""
import json
import re
import sys
import unicodedata
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DLIVE_URL = "https://dlive.sx/"
CDN_URL = "https://api.cdnlivetv.tv/api/v1/channels/?user=cdnlivetv&plan=free"
SALIDA = "agenda_hoy_cdn.txt"

# Placeholders de dlive que no son canales reales.
BASURA = {"event stream", "event sd stream", "extra stream", "channel not listed"}
CALIDAD = {"fhd", "hd", "uhd", "sd", "4k", "h265", "hevc", "raw", "plus", "premium"}
PAISES = {
    "us", "usa", "ca", "uk", "br", "arg", "mx", "pt", "pl", "fr", "de", "nl",
    "nz", "il", "gr", "dk", "se", "fi", "at", "au", "cy", "bg", "ru", "tr",
    "ksa", "ch", "it", "es", "be", "no", "ie", "za", "in", "jp", "kr", "cn",
    "rsa", "mena", "latam", "vip", "ro",
}
# dlive escribe el pais con nombre ("TVP Sport Poland"); el CDN usa el codigo.
ALIAS_PAIS = {
    "poland": "pl", "israel": "il", "england": "uk", "germany": "de",
    "france": "fr", "turkey": "tr", "greece": "gr", "portugal": "pt",
    "netherlands": "nl", "denmark": "dk", "sweden": "se", "finland": "fi",
    "norway": "no", "canada": "ca", "brazil": "br", "argentina": "arg",
    "mexico": "mx", "spain": "es", "italy": "it", "cyprus": "cy",
    "romania": "ro", "bulgaria": "bg", "usa": "us", "uae": "uae",
}


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def _tokens(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r"([a-z])(\d)", r"\1 \2", txt.lower())
    txt = re.sub(r"(\d)([a-z])", r"\1 \2", txt)
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    out = []
    for t in txt.split():
        t = ALIAS_PAIS.get(t, t)
        if t:
            out.append(t)
    return out


def _core(txt):
    """Nombre sin sufijos de calidad/pais: 'TSN 5 FHD CA' -> 'tsn 5'."""
    return " ".join(t for t in _tokens(txt) if t not in CALIDAD and t not in PAISES)


def cargar_catalogo():
    data = json.loads(get(CDN_URL))
    canales = []
    for c in data.get("channels") or []:
        if not c.get("name"):
            continue
        canales.append({
            "name": c["name"],
            "online": (c.get("status") or "").lower() == "online",
            "core": _core(c["name"]),
            "tokens": set(_tokens(c["name"])) - CALIDAD,
        })
    return canales


def _ordenar(lista):
    return sorted(lista, key=lambda c: (not c["online"], c["name"]))


def _relevantes(tokens):
    """Quita tokens que no aportan identidad: el pais y palabras de relleno."""
    return {t for t in tokens if t not in CALIDAD and t not in PAISES and t not in RELLENO}


RELLENO = {"the", "tv", "channel", "network", "sports", "sport", "live", "hd"}


def emparejar(canal_dlive, catalogo):
    """Canales del CDN que corresponden al nombre que usa dlive.

    Regla: TODOS los tokens relevantes del nombre de dlive deben aparecer en el
    nombre del CDN (asi 'Sport 5 Israel' no se confunde con 'Sport 5+ Stars').
    Si dlive nombra el pais, el canal del CDN debe ser de ese pais. Con los
    candidatos se ordena por: menos palabras sobrantes, online primero.
    """
    todos = set(_tokens(canal_dlive))
    claves = _relevantes(todos)
    if not claves:
        return []
    paises = todos & PAISES

    exactos, parciales = [], []
    for c in catalogo:
        ct = c["tokens"]
        if not claves <= ct:
            continue
        if paises and not (paises & ct):
            continue
        sobra = len(ct - todos)
        (exactos if sobra == 0 else parciales).append((sobra, c))

    if exactos:
        return _ordenar([c for _, c in exactos])
    if parciales:
        mejor = min(s for s, _ in parciales)
        return _ordenar([c for s, c in parciales if s == mejor])[:4]

    # Ultimo recurso: 90% de los tokens relevantes presentes.
    puntuados = []
    for c in catalogo:
        if paises and not (paises & c["tokens"]):
            continue
        inter = len(claves & c["tokens"])
        cobertura = inter / len(claves)
        if cobertura >= 0.9:
            puntuados.append((cobertura, len(c["tokens"] - todos), c))
    puntuados.sort(key=lambda x: (-x[0], x[1], not x[2]["online"], x[2]["name"]))
    return [c for _, _, c in puntuados[:3]]



def parsear_dlive(html):
    eventos = []
    patron_bloque = (
        r'class="schedule__event"(.*?)(?=class="schedule__event"|'
        r'class="schedule__(?:day|cat|section)|</table>|\Z)'
    )
    for bloque in re.findall(patron_bloque, html, re.DOTALL):
        mh = re.search(r'data-title="[^"]*?\s(\d{1,2}:\d{2})"', bloque)
        mt = re.search(r'class="schedule__eventTitle">(.*?)</span>', bloque, re.DOTALL)
        if not mt:
            continue
        titulo = re.sub(r"<[^>]+>", "", mt.group(1))
        # Fuera emojis/banderas.
        titulo = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]", "", titulo)
        titulo = re.sub(r"\s+", " ", titulo).strip()

        canales = []
        mc = re.search(r'class="schedule__channels"(.*?)(?=</div>|\Z)', bloque, re.DOTALL)
        if mc:
            for nom in re.findall(r'title="([^"]+)"', mc.group(1)):
                nom = re.sub(r"\s+", " ", nom).strip()
                if not nom or nom.lower() in BASURA or nom in canales:
                    continue
                canales.append(nom)

        if not canales:
            continue

        liga, _, resto = titulo.partition(" : ")
        eventos.append({
            "hora": mh.group(1) if mh else "",
            "liga": liga.strip() or "Otros",
            "partido": (resto or titulo).strip(),
            "titulo": titulo,
            "canales": canales,
        })
    return eventos


def hora_nicaragua(hora):
    """dlive publica en hora UK (GMT en invierno): Nicaragua = GMT-6."""
    try:
        hh, mm = (int(x) for x in hora.split(":"))
    except ValueError:
        return hora
    return f"{(hh - 6) % 24:02d}:{mm:02d}"


def main():
    detalle = "--detalle" in sys.argv
    solo_canales = "--canales" in sys.argv

    catalogo = cargar_catalogo()
    html = get(DLIVE_URL)
    eventos = parsear_dlive(html)

    lineas = []
    resumen = {}
    sin_cdn = 0

    for ev in eventos:
        ok, no, usados = [], [], []
        for canal in ev["canales"]:
            matches = emparejar(canal, catalogo)
            online = [m["name"] for m in matches if m["online"]]
            offline = [m["name"] for m in matches if not m["online"]]
            if online:
                ok.append(f"{canal} -> {', '.join(online)}")
                usados.extend(online)
            elif offline:
                ok.append(f"{canal} -> {', '.join(offline)} (OFERTA AGOTADA/OFF)")
            else:
                no.append(canal)

        if not any(" (OFERTA" not in x for x in ok):
            sin_cdn += 1

        liga = ev["liga"]
        r = resumen.setdefault(liga, {"eventos": 0, "con_cdn": 0, "canales": {}})
        r["eventos"] += 1
        if usados:
            r["con_cdn"] += 1
        for nombre in usados:
            r["canales"][nombre] = r["canales"].get(nombre, 0) + 1

        lineas.append(
            f"[{ev['hora']} UK / {hora_nicaragua(ev['hora'])} NI] {liga} : {ev['partido']}"
        )
        lineas.append(f"   CDN OK : {' | '.join(ok) if ok else '(ninguno)'}")
        if no:
            lineas.append(f"   NO CDN : {', '.join(no)}")
        lineas.append("")

    with open(SALIDA, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas))

    print(f"Catalogo CDN: {len(catalogo)} canales "
          f"({sum(1 for c in catalogo if c['online'])} online)")
    print(f"Eventos en la agenda de dlive (con canal real): {len(eventos)}")
    print(f"Eventos sin ningun canal disponible en el CDN: {sin_cdn}")
    print(f"Detalle completo en {SALIDA}\n")

    if solo_canales:
        todos = {}
        for r in resumen.values():
            for nombre, veces in r["canales"].items():
                todos[nombre] = todos.get(nombre, 0) + veces
        for nombre, veces in sorted(todos.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{veces:>3}x  {nombre}")
        return

    for liga in sorted(resumen, key=lambda l: -resumen[l]["eventos"]):
        r = resumen[liga]
        top = sorted(r["canales"].items(), key=lambda kv: -kv[1])[:4]
        etiqueta = ", ".join(f"{n} x{v}" for n, v in top) or "sin cobertura"
        print(f"{liga:<34} {r['con_cdn']:>2}/{r['eventos']:<3} eventos  |  {etiqueta}")

    if detalle:
        print()
        print("\n".join(lineas))


if __name__ == "__main__":
    main()
