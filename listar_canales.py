"""
Listar todos los canales en la BD con sus alternativas.
"""
import db

rows = db.run_query("SELECT id, name, stream, alternatives FROM channels ORDER BY name")
print(f"Total canales: {len(rows)}")
print()
for r in rows:
    alt_count = len(r['alternatives']) if r['alternatives'] else 0
    print(f"id={r['id']}, name={r['name']}, stream={r['stream']}, alternatives={alt_count}")
