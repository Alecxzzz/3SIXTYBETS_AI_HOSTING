"""
Eliminar canales de ESPN duplicados que no son los principales.
"""
import db

# Canales a eliminar (los que el usuario mencionó)
CANALES_A_ELIMINAR = [
    "3 ESPN_a08t",
    "3 ESPN. 4K_a0u1",
    "3 ESPN2_a08s",
    "4 ESPN 3_a090",
    "4 ESPN 4._a0u0",
]

print("Canales que se eliminarán:")
for name in CANALES_A_ELIMINAR:
    rows = db.run_query("SELECT id, stream, alternatives FROM channels WHERE name = %s", (name,), fetchone=False)
    if len(rows) == 0:
        print(f"  [INFO] {name} no encontrado")
        continue
    
    for r in rows:
        print(f"  [OK] {name} (id={r['id']}) - stream: {r['stream']}")
        db.run_query("DELETE FROM channels WHERE id = %s", (r['id'],))
        print(f"    Eliminado")

print("\nProceso completado.")
