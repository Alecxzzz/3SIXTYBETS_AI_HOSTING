"""
Eliminar canal duplicado 'ESPN 3' de la BD.
"""
import db

# Buscar canales duplicados
rows = db.run_query(
    "SELECT id, name, stream, alternatives FROM channels WHERE name = 'ESPN 3' ORDER BY id"
)

if len(rows) > 1:
    print(f"Encontrados {len(rows)} canales duplicados 'ESPN 3':")
    for r in rows:
        print(f"  id={r['id']}, stream={r['stream']}, alternatives={r['alternatives']}")
    
    # El canal con id 'RXhvnp-9E-A' es el duplicado (no tiene alternativas)
    duplicado_id = 'RXhvnp-9E-A'
    
    ok = db.run_query("DELETE FROM channels WHERE id = %s", (duplicado_id,))
    if ok:
        print(f"\nCanal duplicado eliminado: id={duplicado_id}")
    else:
        print("\nError al eliminar canal duplicado")
else:
    print("No hay canales duplicados 'ESPN 3'")
