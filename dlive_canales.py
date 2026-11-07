"""
Mapa de canales de dlive.sx (y su cadena de resolucion)
======================================================
dlive.sx no es un CDN: su player hace iframe a daddylive (thedaddy.li), que a
su vez embebe un player propio (dembed.st) que pide un token y sirve el m3u8.

Cadena (verificada el 2026-11-06):
    https://dlive.sx/watch.php?id=754                     (pagina del canal)
      -> https://thedaddy.li/stream/stream-754.php        (iframe)
        -> https://dembed.st/premiumtv/daddy.php?id=754   (embed)
          -> GET {EDGE}/api/playlist-token?slug={CHANNEL} -> {"token", "ttl":120}
            -> GET {SRC}?t={token}                        -> m3u8

OJO: el edge exige cabeceras de navegador (Origin + Sec-Fetch-*) y el token va
en el parametro 't'. En el plan gratuito ese m3u8 devuelve un PLACEHOLDER (una
secuencia de imagenes de tiktokcdn), no video: se comprobo con 754 (NBCS
Boston), 777, 755, 765 (MSG), 114 (TSN4), 982 y 1 (NBA League Pass).

Uso:
    python dlive_canales.py NBCS          # busca canales por nombre
    python dlive_canales.py --listar      # todos los canales (nombre -> id)
    python dlive_canales.py --probar 754  # resuelve y dice si es video real
"""
import json
import re
import sys
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DLIVE = "https://dlive.sx"
DADDY = "https://thedaddy.li"
# El edge rechaza peticiones sin pinta de navegador.
NAVEGADOR = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Origin": "https://dembed.st",
    "Referer": "https://dembed.st/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


def get(url, headers=None, timeout=25):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def cargar_mapa():
    """nombre exacto de canal en dlive -> id (sale del HTML de la portada)."""
    html = get(DLIVE + "/")
    mapa = {}
    for m in re.finditer(r'href="/watch\.php\?id=(\d+)"[^>]*title="([^"]+)"', html):
        mapa.setdefault(m.group(2).strip(), m.group(1))
    return mapa


def buscar(texto):
    t = texto.lower()
    return {n: i for n, i in cargar_mapa().items() if t in n.lower()}


def resolver(cid):
    """Devuelve (m3u8, tipo). tipo: 'video' | 'placeholder' | motivo del fallo."""
    html = get(f"{DADDY}/stream/stream-{cid}.php", {"Referer": DLIVE + "/"})
    m = re.search(r'<iframe[^>]+src="(https?://[^"]+)"', html)
    if not m:
        return None, "sin iframe en thedaddy"
    page = get(m.group(1), {"Referer": DADDY + "/"})

    edge = re.search(r'const EDGE = "([^"]+)"', page)
    chan = re.search(r'const CHANNEL = "([^"]+)"', page)
    src = re.search(r'const SRC = "([^"]+)"', page)
    if not (edge and chan and src):
        return None, f"embed sin constantes ({m.group(1)[:60]})"

    token = json.loads(
        get(f"{edge.group(1)}/api/playlist-token?slug={chan.group(1)}", NAVEGADOR)
    )["token"]

    playlist = get(f"{src.group(1)}?t={token}", NAVEGADOR)
    if not playlist.startswith("#EXTM3U"):
        return None, "la playlist no es m3u8"

    segs = [l for l in playlist.splitlines() if l and not l.startswith("#")]
    if not segs:
        return None, "playlist vacia"
    # El plan free sirve una secuencia de imagenes (placeholder), no video.
    tipo = "placeholder" if any(x in segs[0] for x in (".image", ".jpg", ".png")) else "video"
    return f"{src.group(1)}?t={token}", tipo



def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    if args[0] == "--listar":
        mapa = cargar_mapa()
        for nombre, cid in sorted(mapa.items()):
            print(f"{cid:>5}  {nombre}")
        print(f"\n{len(mapa)} canales en dlive")
        return

    if args[0] == "--probar":
        if len(args) < 2:
            print("Falta el id: python dlive_canales.py --probar 754")
            return
        url, tipo = resolver(args[1])
        print(f"id {args[1]} -> {tipo}")
        if url:
            print(f"m3u8: {url}")
        return

    mapa = buscar(" ".join(args))
    if not mapa:
        print(f"Sin resultados para {' '.join(args)!r}")
        return
    for nombre, cid in sorted(mapa.items()):
        print(f"{cid:>5}  {nombre:<38} {DLIVE}/watch.php?id={cid}")
    print(f"\n{len(mapa)} canales")


if __name__ == "__main__":
    main()
