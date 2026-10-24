"""
Agregar evento de Chelsea vs Tottenham a la BD.
"""
import sys
sys.path.insert(0, ".")

import db

# Evento: Chelsea vs Tottenham
evento = {
    "sport": "Football",
    "name": "Chelsea vs Tottenham",
    "stream": "http://38.252.215.33:8000/play/a024/index.m3u8",
    "referer": "https://tu-sitio.com/espn-gratis",
    "type": "m3u8",
}

# Verificar si ya existe
existing = db.run_query(
    "SELECT id FROM events WHERE name = %s",
    (evento["name"],),
    fetchone=True
)

if existing:
    print(f"[INFO] Evento '{evento['name']}' ya existe (id={existing['id']})")
    # Actualizar el stream
    ok = db.run_query(
        "UPDATE events SET stream = %s, created_at = %s WHERE id = %s",
        (evento["stream"], db.now_utc(), existing["id"])
    )
    if ok:
        print(f"[OK] Evento actualizado correctamente")
    else:
        print(f"[ERROR] No se pudo actualizar el evento")
else:
    # Agregar el evento
    ok = db.run_query(
        """
        INSERT INTO events (id, sport, name, stream, referer, type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            db.secrets.token_urlsafe(8),
            evento["sport"],
            evento["name"],
            evento["stream"],
            evento["referer"],
            evento["type"],
            db.now_utc(),
        )
    )
    if ok:
        print(f"[OK] Evento '{evento['name']}' agregado correctamente")
        print(f"  sport: {evento['sport']}")
        print(f"  stream: {evento['stream']}")
        print(f"  type: {evento['type']}")
    else:
        print(f"[ERROR] No se pudo agregar el evento")

print("\nProceso completado.")
