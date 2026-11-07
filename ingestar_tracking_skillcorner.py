"""Ingesta el tracking abierto de SkillCorner (MIT) para el reproductor "Radar HD".

SkillCorner publica 10 partidos de la A-League 2024/25 con tracking real de los
22 jugadores + balon a 10 fps, en Git LFS (~90 MB por partido):

    https://github.com/SkillCorner/opendata   (licencia MIT)

Este script descarga un partido y lo convierte a un JSON compacto y gzipeado
que sirve el backend en /stats/tracking-demo.

Salida (demo_tracking/<id>.json.gz):

    {
      "id": 1874553,
      "fecha": "2024-11-23T06:00:00Z",
      "competicion": "AUS - A-League - 2024/2025",
      "estadio": "Kayo Stadium",
      "marcador": "2-3",
      "local":  {"nombre": "Brisbane FC",     "color": "#363636"},
      "visita": {"nombre": "Adelaide United", "color": "#ffffff"},
      "largo": 105, "ancho": 68,
      "fps": 2.5,
      "balon": [x0, y0, x1, y1, ...],
      "jugadores": [{"n": "J. Yull", "d": 12, "eq": "away", "pos": "AM",
                     "camino": [x0, y0, x1, y1, ...]}, ...]
    }

Coordenadas enteras en decimetros: x 0..1050, y 0..680 (origen arriba-izquierda).
El centinela -1 significa "sin deteccion" en ese frame.

Uso:
    python ingestar_tracking_skillcorner.py 1874553
    python ingestar_tracking_skillcorner.py --todos
"""

import gzip
import json
import os
import sys

import requests

BASE_LFS = (
    "https://media.githubusercontent.com/media/SkillCorner/opendata/master"
    "/data/matches/{mid}/{mid}_{archivo}"
)
BASE_RAW = (
    "https://raw.githubusercontent.com/SkillCorner/opendata/master"
    "/data/matches/{mid}/{mid}_{archivo}"
)

PARTIDOS = [
    1874553, 1886347, 1899585, 1925299, 1927964,
    1953632, 2010085, 2011166, 2013725, 2015213,
]

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_tracking")
PASO_FRAMES = 4      # de 10 fps a 2.5 fps
FPS_SALIDA = 2.5


def _descargar_json(mid):
    r = requests.get(BASE_RAW.format(mid=mid, archivo="match.json"), timeout=60)
    r.raise_for_status()
    return r.json()


def _decodificar(valor, total):
    """Metros con origen en el centro del campo -> decimetros desde 0,0."""
    if valor is None:
        return -1
    dm = round((float(valor) + total / 2.0) * 10.0)
    return max(0, min(int(round(total * 10)), dm))


def procesar(mid):
    meta = _descargar_json(mid)
    largo = float(meta.get("pitch_length") or 105)
    ancho = float(meta.get("pitch_width") or 68)

    # id de jugador -> datos para pintar (nombre, dorsal, equipo, posicion)
    fichas = {}
    id_local = (meta.get("home_team") or {}).get("id")
    for p in meta.get("players") or []:
        fichas[str(p.get("id"))] = {
            "n": p.get("short_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "d": p.get("number"),
            "eq": "home" if p.get("team_id") == id_local else "away",
            "pos": ((p.get("player_role") or {}).get("acronym") or ""),
        }

    url = BASE_LFS.format(mid=mid, archivo="tracking_extrapolated.jsonl")
    print(f"[{mid}] descargando tracking (LFS, ~90 MB)...", flush=True)
    r = requests.get(url, timeout=300, stream=True)
    r.raise_for_status()

    orden = []      # ids de jugador en orden estable (22)
    caminos = {}    # id -> [x, y, x, y, ...]
    balon = []
    frames = 0
    leidos = 0

    for linea in r.iter_lines(decode_unicode=True):
        if not linea:
            continue
        leidos += 1
        if leidos % PASO_FRAMES != 0:
            continue
        try:
            fr = json.loads(linea)
        except ValueError:
            continue
        if fr.get("timestamp") is None or fr.get("period") is None:
            continue

        pd = fr.get("player_data") or []
        if not pd:
            continue

        if not orden:
            orden = [str(x.get("player_id")) for x in pd]
            for pid in orden:
                caminos[pid] = []

        por_id = {str(x.get("player_id")): x for x in pd}
        for pid in orden:
            dato = por_id.get(pid)
            if not dato:
                caminos[pid].extend([-1, -1])
                continue
            caminos[pid].extend([
                _decodificar(dato.get("x"), largo),
                _decodificar(dato.get("y"), ancho),
            ])

        b = fr.get("ball_data") or {}
        balon.extend([
            _decodificar(b.get("x"), largo),
            _decodificar(b.get("y"), ancho),
        ])
        frames += 1

    if not frames:
        raise SystemExit(f"[{mid}] no se pudo leer ningun frame con datos")

    jugadores = []
    sin_ficha = 0
    for pid in orden:
        f = fichas.get(pid)
        if not f:
            sin_ficha += 1
            f = {"n": pid, "d": None, "eq": "home", "pos": ""}
        jugadores.append({
            "n": f["n"],
            "d": f["d"],
            "eq": f["eq"],
            "pos": f["pos"],
            "camino": caminos[pid],
        })
    if sin_ficha:
        print(f"[{mid}] aviso: {sin_ficha} jugadores sin ficha en match.json", flush=True)
    return _guardar(mid, meta, largo, ancho, jugadores, balon, frames)


def _guardar(mid, meta, largo, ancho, jugadores, balon, frames):
    comp = meta.get("competition_edition") or {}
    salida = {
        "id": mid,
        "fecha": meta.get("date_time"),
        "competicion": comp.get("name") or (comp.get("competition") or {}).get("name") or "",
        "estadio": (meta.get("stadium") or {}).get("name") or "",
        "marcador": f"{meta.get('home_team_score')}-{meta.get('away_team_score')}",
        "local": {
            "nombre": (meta.get("home_team") or {}).get("short_name")
            or (meta.get("home_team") or {}).get("name") or "Local",
            "color": (meta.get("home_team_kit") or {}).get("jersey_color") or "#22c55e",
        },
        "visita": {
            "nombre": (meta.get("away_team") or {}).get("short_name")
            or (meta.get("away_team") or {}).get("name") or "Visitante",
            "color": (meta.get("away_team_kit") or {}).get("jersey_color") or "#f97316",
        },
        "largo": int(round(largo)),
        "ancho": int(round(ancho)),
        "fps": FPS_SALIDA,
        "balon": balon,
        "jugadores": jugadores,
    }

    os.makedirs(SALIDA, exist_ok=True)
    destino = os.path.join(SALIDA, f"{mid}.json.gz")
    crudo = json.dumps(salida, separators=(",", ":")).encode("utf-8")
    with open(destino, "wb") as fh:
        fh.write(gzip.compress(crudo, 6))
    print(
        f"[{mid}] OK: {frames} frames (~{frames / FPS_SALIDA / 60:.0f} min), "
        f"{len(jugadores)} jugadores, {len(crudo) / 1e6:.1f} MB -> "
        f"{os.path.getsize(destino) / 1e6:.2f} MB gz",
        flush=True,
    )
    return salida


def actualizar_indice(nuevos):
    ruta = os.path.join(SALIDA, "index.json")
    actual = []
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as fh:
                actual = json.load(fh)
        except (ValueError, OSError):
            actual = []

    por_id = {m["id"]: m for m in actual if isinstance(m, dict) and "id" in m}
    for datos in nuevos:
        por_id[datos["id"]] = {
            "id": datos["id"],
            "fecha": datos["fecha"],
            "competicion": datos["competicion"],
            "estadio": datos["estadio"],
            "marcador": datos["marcador"],
            "local": datos["local"]["nombre"],
            "visita": datos["visita"]["nombre"],
            "minutos": round(len(datos["balon"]) / 2 / datos["fps"] / 60),
        }

    lista = sorted(por_id.values(), key=lambda m: str(m.get("fecha") or ""))
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(lista, fh, ensure_ascii=False, indent=1)
    print(f"index.json -> {len(lista)} partidos", flush=True)


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        ids = [PARTIDOS[0]]
    elif args[0] == "--todos":
        ids = PARTIDOS
    else:
        ids = [int(a) for a in args]

    actualizar_indice([procesar(mid) for mid in ids])


if __name__ == "__main__":
    main()
