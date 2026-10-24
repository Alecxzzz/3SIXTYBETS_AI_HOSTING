"""
Verificar canales con alternativas en la BD.
"""
import db

# Verificar todos los canales con alternativas
rows = db.run_query(
    "SELECT id, name, stream, alternatives FROM channels WHERE alternatives IS NOT NULL AND alternatives != '' ORDER BY name"
)
print(f"Total canales con alternativas: {len(rows)}")
for r in rows:
    alt_count = len(r['alternatives']) if r['alternatives'] else 0
    print(f"id={r['id']}, name={r['name']}: {alt_count} alternativas")
    print(f"  stream: {r['stream']}")
    print()

