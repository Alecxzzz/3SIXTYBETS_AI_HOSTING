"""
Script para agregar canales principales a la BD con sus alternativas.

Este script añade los canales principales (ESPN 1, ESPN 2, ESPN 3, ESPN 4)
a la tabla 'channels' con la columna 'alternatives' que contiene las URLs
de los canales alternativos definidos en channels.js.
"""
import sys
sys.path.insert(0, ".")

import db
import json

# Definir los canales principales y sus alternativas
# Mapa: nombre_canalone -> lista de URLs de alternativas
CHANNELS_PRINCIPALES = {
    "ESPN 1": [
        "http://38.252.215.33:8000/play/a00j/index.m3u8",
        "http://138.59.177.78:9999/play/a003/index.m3u8",
        "https://6.ftlly.com/espn4/mono.m3u8?token=b3e3e6a8ec7c7ee815e6321fb59ff34ae8fdf674-5a-1788727529-1788709529",
        "https://barinaslive1.boldmss.com/ESPN_5/ESPN_5.isml/ESPN_5.m3u8",
        "https://barinaslive1.boldmss.com/ESPN_6/ESPN_6.isml/ESPN_6.m3u8",
        "http://38.252.215.33:8000/play/a024/index.m3u8",
        "https://dw5pdgvk.fubo18.com/espndeportes/mono.m3u8?token=6a7c6af6683bf5536cf39fd7393cea0ba873a585-33-1788727949-1788709949",
        "http://45.228.234.9:8000/play/a05c/index.m3u8",
        "http://stream.bottledesk.net/6500/index.m3u8",
        "https://barinaslive1.boldmss.com/ESPN_7/ESPN_7.isml/ESPN_7.m3u8",
    ],
    "ESPN 2": [
        "http://38.252.215.33:8000/play/a00k/index.m3u8",
        "http://45.228.234.9:8000/play/a05g/index.m3u8",
        "http://190.61.47.54:8000/play/a005/index.m3u8",
        "http://45.228.234.9:8000/play/a05c/index.m3u8",
        "http://181.224.255.210:8001/play/a0pt/index.m3u8",
        "http://138.59.177.78:9999/play/a004/index.m3u8",
    ],
    "ESPN 3": [
        "http://45.228.234.9:8000/play/a05h/index.m3u8",
        "http://190.83.2.182:8090/ESPN3/index.m3u8",
        "http://38.252.215.33:8000/play/a00l/index.m3u8",
        "http://190.61.47.54:8000/play/a02u/index.m3u8",
        "http://138.59.177.78:9999/play/a07v/index.m3u8",
        "http://138.59.177.78:9999/play/a0dd/index.m3u8",
        "http://138.59.177.78:9999/play/a0n5/index.m3u8",
    ],
    "ESPN 4": [
        "http://38.252.215.33:8000/play/a00m/index.m3u8",
        "http://45.228.234.9:8000/play/a05j/index.m3u8",
        "http://190.61.47.54:8000/play/a02v/index.m3u8",
        "http://138.59.177.78:9999/play/a07w/index.m3u8",
        "http://138.59.177.78:9999/play/a0cv/index.m3u8",
        "http://181.224.255.210:8001/play/a0og/index.m3u8",
        "http://138.59.177.78:9999/play/a0lw/index.m3u8",
    ],
    "CLARO SPORTS": [
        "http://138.59.177.78:9999/play/a0bk/index.m3u8",
        "http://138.59.177.78:9999/play/a0ey/index.m3u8",
    ],
    "DIRECTV SPORTS": [
        "http://181.224.255.210:8001/play/a0s8/index.m3u8",
    ],
    "SPOR TV 1 BR": [
        "http://fente.lista.luxe/live/iptvgood/101011/3810.m3u8",
    ],
    "SPOR TV 3 BR": [
        "http://fente.lista.luxe/live/iptvgood/101011/3806.m3u8",
    ],
    "SPORTV 2 BR": [
        "http://fente.lista.luxe/live/iptvgood/101011/3808.m3u8",
    ],
    "TUDN": [
        "http://138.59.177.78:9999/play/a07z/index.m3u8",
    ],
}

# URLs principales de cada canal (para los canales que tienen stream en BD)
CHANNELS_PRIMARY = {
    "ESPN 1": "https://barinaslive1.boldmss.com/ESPN/ESPN.isml/ESPN.m3u8",
    "ESPN 2": "https://barinaslive1.boldmss.com/ESPN_2/ESPN_2.isml/ESPN_2.m3u8",
    "ESPN 3": "https://barinaslive1.boldmss.com/ESPN_3/ESPN_3.isml/ESPN_3.m3u8",
    "ESPN 4": "https://barinaslive1.boldmss.com/ESPN_4/ESPN_4.isml/ESPN_4.m3u8",
}


def main():
    print("Comenzando a agregar canales principales a la BD...\n")
    
    # Verificar si la columna alternatives existe
    rows = db.run_query(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'channels' AND COLUMN_NAME = 'alternatives'"
    )
    if len(rows) == 0:
        print("ERROR: La columna 'alternatives' no existe en la tabla channels")
        print("Ejecutar primero add_alternatives_column.py")
        return
    
    for nombre, alternativas in CHANNELS_PRINCIPALES.items():
        # Verificar si el canal ya existe
        existing = db.run_query(
            "SELECT id FROM channels WHERE name = %s", (nombre,), fetchone=True
        )
        
        if existing:
            print(f"  [INFO] {nombre} ya existe (id={existing['id']})")
            # Actualizar las alternativas
            ok = db.run_query(
                "UPDATE channels SET alternatives = %s, updated_at = %s WHERE id = %s",
                (json.dumps(alternativas), db.now_utc(), existing['id'])
            )
            if ok:
                print(f"    [OK] Actualizadas alternativas para {nombre}")
            else:
                print(f"    [ERROR] No se pudieron actualizar alternativas para {nombre}")
        else:
            # Agregar el canal principal
            ok = db.run_query(
                """
                INSERT INTO channels (id, name, stream, type, status, ads, use_proxy, geo_restriction, alternatives, created_at, updated_at)
                VALUES (%s, %s, %s, 'm3u8', 'ACTIVO', false, false, 'NONE', %s, %s, %s)
                """,
                (
                    db.secrets.token_urlsafe(8),
                    nombre,
                    CHANNELS_PRIMARY.get(nombre, ""),
                    json.dumps(alternativas),
                    db.now_utc(),
                    db.now_utc(),
                )
            )
            if ok:
                print(f"  [OK] {nombre} agregado con {len(alternativas)} alternativas")
            else:
                print(f"  [ERROR] No se pudo agregar {nombre}")
    
    print("\nProceso completado.")


if __name__ == "__main__":
    main()
