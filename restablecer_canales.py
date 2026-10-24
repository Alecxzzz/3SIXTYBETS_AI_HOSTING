"""
Solución definitiva: eliminar los canales duplicados y restaurar las alternativas correctas.
"""
import db
import json

# 1. Eliminar todos los canales que tienen las alternativas incorrectas (muchas de más)
#    Estos son los canales que el script add_channels_principales.py modificó incorrectamente
WRONG_ALTERNATIVES = {
    "CLARO SPORTS": 100,
    "DIRECTV SPORTS": 52,
    "ESPN 1": 685,
    "ESPN 2": 299,
    "ESPN 3": 343,
    "ESPN 4": 350,
    "SPOR TV 1 BR": 58,
    "SPOR TV 3 BR": 58,
    "SPORTV 2 BR": 58,
    "TUDN": 50,
}

for nombre, expected_count in WRONG_ALTERNATIVES.items():
    rows = db.run_query("SELECT id, stream, alternatives FROM channels WHERE name = %s ORDER BY id", (nombre,), fetchone=False)
    if len(rows) == 0:
        print(f"[INFO] {nombre} no encontrado")
        continue
    
    # Si los valores actuales coinciden con los esperados, no hacer nada
    if len(rows) == 1:
        row = rows[0]
        if row['alternatives'] and len(row['alternatives']) == expected_count:
            print(f"[INFO] {nombre} ya tiene {expected_count} alternativas correctas")
            continue
    
    print(f"[INFO] {nombre}: {len(rows)} entradas, eliminando todos")
    for r in rows:
        db.run_query("DELETE FROM channels WHERE id = %s", (r['id'],))
    
    # Agregar el canal correcto
    if nombre == "CLARO SPORTS":
        stream = "https://d1seb4wyirpp71.cloudfront.net/live/75898381-5c0a-4506-8ce0-98959aed7356/live.isml/live-audio_0=96000-video=2000000.m3u8"
        alternatives = [
            "http://138.59.177.78:9999/play/a0bk/index.m3u8",
            "http://138.59.177.78:9999/play/a0ey/index.m3u8",
        ]
    elif nombre == "DIRECTV SPORTS":
        stream = "http://181.224.255.210:8001/play/a0s8/index.m3u8"
        alternatives = [
            "http://181.224.255.210:8001/play/a0s8/index.m3u8",
        ]
    elif nombre == "SPOR TV 1 BR":
        stream = "http://fonte.lista.luxe/live/iptvgood/101011/3811.m3u8"
        alternatives = [
            "http://fente.lista.luxe/live/iptvgood/101011/3810.m3u8",
        ]
    elif nombre == "SPOR TV 3 BR":
        stream = "http://fente.lista.luxe/live/iptvgood/101011/3806.m3u8"
        alternatives = [
            "http://fente.lista.luxe/live/iptvgood/101011/3806.m3u8",
        ]
    elif nombre == "SPORTV 2 BR":
        stream = "http://fente.lista.luxe/live/iptvgood/101011/3809.m3u8"
        alternatives = [
            "http://fente.lista.luxe/live/iptvgood/101011/3808.m3u8",
        ]
    elif nombre == "TUDN":
        stream = "http://200.115.120.1:8000/play/ca039/index.m3u8"
        alternatives = [
            "http://138.59.177.78:9999/play/a07z/index.m3u8",
        ]
    elif nombre == "ESPN 1":
        stream = "https://barinaslive1.boldmss.com/ESPN/ESPN.isml/ESPN.m3u8"
        alternatives = [
            "http://38.252.215.33:8000/play/a00j/index.m3u8",
            "http://138.59.177.78:9999/play/a003/index.m3u8",
        ]
    elif nombre == "ESPN 2":
        stream = "https://barinaslive1.boldmss.com/ESPN_2/ESPN_2.isml/ESPN_2.m3u8"
        alternatives = [
            "http://38.252.215.33:8000/play/a00k/index.m3u8",
            "http://190.61.47.54:8000/play/a005/index.m3u8",
        ]
    elif nombre == "ESPN 3":
        stream = "https://barinaslive1.boldmss.com/ESPN_3/ESPN_3.isml/ESPN_3.m3u8"
        alternatives = [
            "http://45.228.234.9:8000/play/a05h/index.m3u8",
            "http://190.61.47.54:8000/play/a02u/index.m3u8",
        ]
    elif nombre == "ESPN 4":
        stream = "https://barinaslive1.boldmss.com/ESPN_4/ESPN_4.isml/ESPN_4.m3u8"
        alternatives = [
            "http://38.252.215.33:8000/play/a00m/index.m3u8",
            "http://190.61.47.54:8000/play/a02v/index.m3u8",
        ]
    else:
        continue
    
    ok = db.run_query(
        "INSERT INTO channels (id, name, stream, type, status, ads, use_proxy, geo_restriction, alternatives, created_at, updated_at) VALUES (%s, %s, %s, 'm3u8', 'ACTIVO', false, false, 'NONE', %s, %s, %s)",
        (
            db.secrets.token_urlsafe(8),
            nombre,
            stream,
            json.dumps(alternatives),
            db.now_utc(),
            db.now_utc(),
        )
    )
    if ok:
        print(f"[OK] {nombre} agregado con {len(alternatives)} alternativas")
    else:
        print(f"[ERROR] No se pudo agregar {nombre}")

print("\nProceso completado.")
