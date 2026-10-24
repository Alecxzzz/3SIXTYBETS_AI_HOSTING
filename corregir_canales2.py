"""
Forzar corrección de canales CLARO SPORTS y TUDN.
"""
import db
import json

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
    
    print(f"[INFO] {nombre}: {len(rows)} entradas")
    for r in rows:
        print(f"  id={r['id']}")
    
    # Eliminar todos los canales con este nombre
    for r in rows:
        db.run_query("DELETE FROM channels WHERE id = %s", (r['id'],))
        print(f"  Eliminado: {r['id']}")
    
    # Agregar el canal correcto
    # Buscar el stream de un canal existente con un nombre similar
    similar_rows = db.run_query(
        "SELECT id, stream FROM channels WHERE name LIKE %s LIMIT 1",
        (f"%{nombre}%",),
        fetchone=True
    )
    
    if similar_rows:
        stream = similar_rows['stream']
        print(f"  Usando stream de canal similar: {stream}")
    else:
        # Usar stream predeterminado
        if nombre == "CLARO SPORTS":
            stream = "https://d1seb4wyirpp71.cloudfront.net/live/75898381-5c0a-4506-8ce0-98959aed7356/live.isml/live-audio_0=96000-video=2000000.m3u8"
        else:
            stream = "http://200.115.120.1:8000/play/ca039/index.m3u8"
        print(f"  Usando stream predeterminado: {stream}")
    
    ok = db.run_query(
        "INSERT INTO channels (id, name, stream, type, status, ads, use_proxy, geo_restriction, alternatives, created_at, updated_at) VALUES (%s, %s, %s, 'm3u8', 'ACTIVO', false, false, 'NONE', %s, %s, %s)",
        (
            db.secrets.token_urlsafe(8),
            nombre,
            stream,
            json.dumps(datos['alternatives']),
            db.now_utc(),
            db.now_utc(),
        )
    )
    if ok:
        print(f"[OK] {nombre} agregado correctamente")
    else:
        print(f"[ERROR] No se pudo agregar {nombre}")

print("\nProceso completado.")
