"""
Migra los 49 canales IPTV (IDs 62-110 de channels.js) a la tabla `channels`
de la base de datos del backend, de modo que aparezcan en GET /channels.

Uso:
    1. Configura MYSQL_* (o MYSQL_URL) en .env con credenciales de la BD remota (Aiven/Northflank).
    2. Ejecuta:  python add_49_channels_to_backend.py

Mapeo de campos frontend -> backend:
  - name              -> name
  - stream            -> stream
  - type ("m3u8")     -> ctype
  - ads (false)       -> ads
  - useProxy (true)   -> use_proxy
  - geoRestriction    -> geo_restriction
  - id (secuencial)   -> NO se usa; el backend genera id = token_urlsafe(8)
  - status            -> siempre 'ACTIVO' (insert hardcodeado en db.create_channel)

Todos estos canales usan http://, por lo que use_proxy = True.
"""
import sys
import os

# Asegura que se importe db.py del directorio actual
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

# (name, stream) — los 49 canales, IDs 62-110 de channels.js.
# Todos: status=ACTIVO, type=m3u8, geoRestriction=NONE, useProxy=true, ads=false.
CANALES = [
    # 62-67 (ESPN HD familia, host 181.224.255.210:8001)
    ("ESPN HD", "http://181.224.255.210:8001/play/a0sd/index.m3u8"),
    ("ESPN Premium HD", "http://181.224.255.210:8001/play/a0pt/index.m3u8"),
    ("ESPN2 HD", "http://181.224.255.210:8001/play/a0oj/index.m3u8"),
    ("ESPN4 HD", "http://181.224.255.210:8001/play/a0og/index.m3u8"),
    ("ESPN5 HD", "http://181.224.255.210:8001/play/a0pr/index.m3u8"),
    ("ESPN6 HD", "http://181.224.255.210:8001/play/a0eq/index.m3u8"),
    # 68-77 (ESPN/DIRECTV, hosts 181.224.255.210:8001 y 190.61.47.54:8000)
    ("ESPN7 HD", "http://181.224.255.210:8001/play/a0m5/index.m3u8"),
    ("ESPN", "http://190.61.47.54:8000/play/a004/index.m3u8"),
    ("ESPN 2", "http://190.61.47.54:8000/play/a005/index.m3u8"),
    ("ESPN 3", "http://190.61.47.54:8000/play/a02u/index.m3u8"),
    ("ESPN 4", "http://190.61.47.54:8000/play/a02v/index.m3u8"),
    ("ESPN 5 Sur (ESPN07)", "http://190.61.47.54:8000/play/a00a/index.m3u8"),
    ("ESPN 6 Sur (ESPN 09)", "http://190.61.47.54:8000/play/a00b/index.m3u8"),
    ("ESPN 7 Sur (ESPN 10)", "http://190.61.47.54:8000/play/a033/index.m3u8"),
    ("DIRECTV SPORTS", "http://181.224.255.210:8001/play/a0s8/index.m3u8"),
    ("ESPN PERU HD", "http://181.224.255.210:8001/play/a0sa/index.m3u8"),
    # 78-83 (SPOR TV BR)
    ("SPOR TV 1 BR", "http://fonte.lista.luxe/live/iptvgood/101011/3811.m3u8"),
    ("SPOR TV 1 BR", "http://fente.lista.luxe/live/iptvgood/101011/3810.m3u8"),
    ("SPORTV 2 BR", "http://fente.lista.luxe/live/iptvgood/101011/3809.m3u8"),
    ("SPORTV 2 BR", "http://fente.lista.luxe/live/iptvgood/101011/3808.m3u8"),
    ("SPOR TV 3 BR", "http://fente.lista.luxe/live/iptvgood/101011/3807.m3u8"),
    ("SPOR TV 3 BR", "http://fente.lista.luxe/live/iptvgood/101011/3806.m3u8"),
    # 84-110 (host 138.59.177.78:9999)
    ("ESPN 5 HD_a0uh", "http://138.59.177.78:9999/play/a0ko/index.m3u8"),
    ("ESPN 2._a0u2", "http://138.59.177.78:9999/play/a0lk/index.m3u8"),
    ("3 ESPN. 4K_a0u1", "http://138.59.177.78:9999/play/a0ll/index.m3u8"),
    ("3 ESPN2_a08s", "http://138.59.177.78:9999/play/a0lm/index.m3u8"),
    ("3 ESPN_a08t", "http://138.59.177.78:9999/play/a0lj/index.m3u8"),
    ("4 ESPN 3_a090", "http://138.59.177.78:9999/play/a0lv/index.m3u8"),
    ("4 ESPN 4._a0u0", "http://138.59.177.78:9999/play/a0lw/index.m3u8"),
    ("5 ESPN  4 HD_a0uc", "http://138.59.177.78:9999/play/a0mh/index.m3u8"),
    ("ESPN  4 HD", "http://138.59.177.78:9999/play/a0cv/index.m3u8"),
    ("ESPN 1 IXP", "http://138.59.177.78:9999/play/a003/index.m3u8"),
    ("FOX SPORT", "http://138.59.177.78:9999/play/a080/index.m3u8"),
    ("FOX SPORT 2-IXP", "http://138.59.177.78:9999/play/a081/index.m3u8"),
    ("TUDN-IXP", "http://138.59.177.78:9999/play/a07z/index.m3u8"),
    ("ESPN 2 IXP", "http://138.59.177.78:9999/play/a004/index.m3u8"),
    ("ESPN 3", "http://138.59.177.78:9999/play/a0dd/index.m3u8"),
    ("ESPN 3-IXP", "http://138.59.177.78:9999/play/a07v/index.m3u8"),
    ("ESPN 4-IXP", "http://138.59.177.78:9999/play/a07w/index.m3u8"),
    ("ESPN 5", "http://138.59.177.78:9999/play/a0ez/index.m3u8"),
    ("ESPN 5-IXP", "http://138.59.177.78:9999/play/a07x/index.m3u8"),
    ("ESPN 6", "http://138.59.177.78:9999/play/a0cs/index.m3u8"),
    ("ESPN 6-IXP", "http://138.59.177.78:9999/play/a07y/index.m3u8"),
    ("5 ESPN 6_a09o", "http://138.59.177.78:9999/play/a0mi/index.m3u8"),
    ("6 ESPN 3  4k_a09x", "http://138.59.177.78:9999/play/a0n5/index.m3u8"),
    ("6 ESPN 6  4k_a0a0", "http://138.59.177.78:9999/play/a0n6/index.m3u8"),
    ("Claro Sports 1 HD", "http://138.59.177.78:9999/play/a0bk/index.m3u8"),
    ("Claro Sports 1 SD", "http://138.59.177.78:9999/play/a0ey/index.m3u8"),
    ("6 ESPN HD 4k_a0ud", "http://138.59.177.78:9999/play/a0n7/index.m3u8"),
]

assert len(CANALES) == 49, f"Se esperaban 49 canales, se encontraron {len(CANALES)}"


def main():
    print(f"Conectando a la BD y creando {len(CANALES)} canales...\n")
    creados = 0
    fallidos = 0
    for name, stream in CANALES:
        try:
            channel = db.create_channel(
                name=name,
                stream=stream,
                ctype="m3u8",
                ads=False,
                use_proxy=True,
                geo_restriction="NONE",
            )
            if channel:
                creados += 1
                print(f"  [OK] {name} -> id={channel['id']}")
            else:
                fallidos += 1
                print(f"  [FAIL] {name} -> db.create_channel devolvió None")
        except Exception as exc:  # noqa: BLE001
            fallidos += 1
            print(f"  [ERROR] {name} -> {exc}")

    print(f"\nResultado: {creados} creados, {fallidos} fallidos de {len(CANALES)}.")
    if fallidos:
        sys.exit(1)


if __name__ == "__main__":
    main()
