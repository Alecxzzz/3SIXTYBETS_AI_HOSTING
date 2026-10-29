# -*- coding: utf-8 -*-
"""
Alta de canales nuevos pedidos por el usuario (frontend + backend).

Sondeados antes de darlos de alta (diagnostico_canales.py):
  - bigboss.bemtv.cn  -> https + Access-Control-Allow-Origin: * + H.264/AAC
                         => DIRECTO (ni proxy ni transcodificador)
  - tinyurl ESPN Dep. -> redirige a http://forever.megogo.xyz/... => PROXY
  - DAZN 23.237.104.106:8080 -> http + H.264 => PROXY (sin transcode)
  - DAZN1 (50.7.120.34:8080) -> HTTP 404: fuente caida (se da de alta igual
                                 porque la pidio el usuario, pero queda avisada)

Ademas, "Fox Sports ESPANOL" (bigboss 22883) se añade como OPCION 2 de
FOX DEPORTES (ESPAÑOL): su stream actual (a080) es HEVC/H.265 y necesita
transcodificador, asi que esta alternativa H.264 le da salida directa.

Uso:
    python agregar_canales_nuevos.py            # dry-run
    python agregar_canales_nuevos.py --apply    # aplica
"""

import json
import sys

import db


BIGBOSS = "https://bigboss.bemtv.cn/STREAM/"
TINYURL = "https://tinyurl.com/SaSha-48/30063/index.m3u8"
RESPALDO = "respaldo_canales_nuevos.json"

# (nombre, stream, alternatives, use_proxy)
NUEVOS = [
    ("Bein Sports USA", f"{BIGBOSS}1716/playlist.m3u8", [], False),
    ("TNT Sports", f"{BIGBOSS}412/playlist.m3u8", [], False),
    # Opcion 2 en http (tinyurl -> forever.megogo.xyz): el proxy hace falta para
    # que la alternativa reproduzca, y el proxy sirve igual el principal https.
    ("ESPN Deportes HD", f"{BIGBOSS}732/playlist.m3u8", [TINYURL], True),
    ("DAZN1", "http://50.7.120.34:8080/DAZN1/index.m3u8", [], True),
    ("DAZN_3", "http://23.237.104.106:8080/DAZN_3/index.m3u8", [], True),
    ("DAZN_4", "http://23.237.104.106:8080/DAZN_4/index.m3u8", [], True),
    ("DAZN_5", "http://23.237.104.106:8080/DAZN_5/index.m3u8", [], True),
]

# (canal_existente, url_a_añadir_como_opcion_2)
OPCIONES = [
    ("FOX DEPORTES (ESPAÑOL)", f"{BIGBOSS}22883/playlist.m3u8"),
]


def _serializable(row):
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


def _opciones_actuales(row):
    crudo = row.get("alternatives") or "[]"
    try:
        datos = json.loads(crudo)
        return [str(x) for x in datos] if isinstance(datos, list) else []
    except (ValueError, TypeError):
        return []


def main() -> int:
    apply = "--apply" in sys.argv

    rows = db.run_query("select * from channels order by name asc") or []
    por_nombre = {r["name"]: r for r in rows}
    print(f"Canales en la BD ahora mismo: {len(rows)}\n")

    altas, cambios = [], []
    for nombre, stream, alts, proxy in NUEVOS:
        if nombre in por_nombre:
            print(f"  [AVISO] {nombre!r} ya existe: se salta")
            continue
        altas.append((nombre, stream, alts, proxy))

    for nombre, url in OPCIONES:
        row = por_nombre.get(nombre)
        if not row:
            print(f"  [AVISO] {nombre!r} no esta en la BD: no se puede añadir la opcion 2")
            continue
        actuales = _opciones_actuales(row)
        if url in actuales:
            print(f"  [AVISO] {nombre!r} ya tiene esa opcion 2")
            continue
        cambios.append((nombre, row, actuales + [url]))

    print("-" * 96)
    for nombre, stream, alts, proxy in altas:
        print(f"  [ALTA]   {nombre:<20} {stream}")
        print(f"           use_proxy={str(proxy).lower()}  opciones={alts or '[]'}")
    for nombre, row, alts in cambios:
        print(f"  [OPCION] {nombre:<20} opciones -> {alts}")
    print("-" * 96)

    if not altas and not cambios:
        print("Nada que hacer.")
        return 0

    if not apply:
        print(f"DRY-RUN: {len(altas)} alta(s) y {len(cambios)} cambio(s). Corre con --apply.")
        return 0

    with open(RESPALDO, "w", encoding="utf-8") as fh:
        json.dump([_serializable(r) for r in rows], fh, ensure_ascii=False, indent=2)
    print(f"Respaldo de {len(rows)} fila(s) en {RESPALDO}")

    fallos = 0
    for nombre, stream, alts, proxy in altas:
        ok = db.run_query(
            """
            insert into channels
            (id, name, stream, type, status, ads, use_proxy, geo_restriction,
             alternatives, created_at, updated_at)
            values (%s, %s, %s, 'm3u8', 'ACTIVO', false, %s, 'NONE', %s, %s, %s)
            """,
            (db.secrets.token_urlsafe(8), nombre, stream, proxy,
             json.dumps(alts), db.now_utc(), db.now_utc()),
        )
        if not ok:
            fallos += 1
            print(f"  [ERROR] no se pudo dar de alta {nombre}")

    for nombre, row, alts in cambios:
        ok = db.run_query(
            "update channels set alternatives = %s, updated_at = %s where id = %s",
            (json.dumps(alts), db.now_utc(), row["id"]),
        )
        if not ok:
            fallos += 1
            print(f"  [ERROR] no se pudieron actualizar las opciones de {nombre}")

    finales = db.run_query(
        "select name, stream, alternatives, use_proxy from channels order by name asc"
    ) or []
    print("-" * 96)
    print(f"RESULTADO: {len(altas) + len(cambios) - fallos} cambio(s) aplicados. "
          f"Canales en la BD: {len(finales)}")
    for r in finales:
        print(f"  - {r['name']:<24} proxy={int(r['use_proxy'])} "
              f"opciones={r['alternatives'] or '[]'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
