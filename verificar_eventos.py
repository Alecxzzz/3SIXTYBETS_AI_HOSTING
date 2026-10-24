"""
Verificar eventos en la BD.
"""
import db

rows = db.run_query(
    "SELECT id, sport, name, stream, type FROM events ORDER BY name"
)
print(f"Total eventos: {len(rows)}")
for r in rows:
    print(f"id={r['id'][:8]}... name={r['name']}, sport={r['sport']}, stream={r['stream']}, type={r['type']}")
