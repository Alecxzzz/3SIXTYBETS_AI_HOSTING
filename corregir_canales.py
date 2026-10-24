"""
Corregir canales con alternativas incorrectas en la BD.
"""
import db
import json

# Canales correctos con sus alternativas
CORRECCIONES = {
    "CLARO SPORTS": {
        "stream": "https://d1seb4wyirpp71.cloudfront.net/live/75898381-5c0a-4506-8ce0-98959aed7356/live.isml/live-audio_0=96000-video=2000000.m3u8",
        "alternatives": [
            "http://138.59.177.78:9999/play/a0bk/index.m3u8",
            "http://138.59.177.78:9999/play/a0ey/index.m3u8",
        ],
    },
    "TUDN": {
        "stream": "http://200.115.120.1:8000/play/ca039/index.m3u8",
        "alternatives": [
            "http://138.59.177.78:9999/play/a07z/index.m3u8",
        ],
    },
}

for nombre, datos in CORRECCIONES.items():
    # Buscar canales con este nombre
    rows = db.run_query("SELECT id FROM channels WHERE name = %s", (nombre,), fetchone=False)
    if len(rows) == 0:
        print(f"[INFO] {nombre} no encontrado")
        continue
    
    for r in rows:
        ok = db.run_query(
            "UPDATE channels SET stream = %s, alternatives = %s, updated_at = %s WHERE id = %s",
            (datos['stream'], json.dumps(datos['alternatives']), db.now_utc(), r['id'])
        )
        if ok:
            print(f"[OK] {nombre} (id={r['id']}) actualizado correctamente")
            print(f"  stream: {datos['stream']}")
            print(f"  alternativas: {datos['alternatives']}")
        else:
            print(f"[ERROR] No se pudo actualizar {nombre} (id={r['id']})")

print("\nProceso completado.")
