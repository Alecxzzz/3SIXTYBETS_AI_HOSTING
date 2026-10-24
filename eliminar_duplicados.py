"""
Eliminar canales duplicados en la BD.
Luego ejecutar corregir_canales.py
"""
import db

# Canales que deben tener solo 1 entrada
DUPICATES = [
    "CLARO SPORTS",
    "DIRECTV SPORTS",
    "SPOR TV 1 BR",
    "SPOR TV 3 BR",
    "SPORTV 2 BR",
    "TUDN",
]

for nombre in DUPICATES:
    rows = db.run_query("SELECT id, stream, alternatives FROM channels WHERE name = %s ORDER BY id", (nombre,), fetchone=False)
    if len(rows) == 0:
        print(f"[INFO] {nombre} no encontrado")
        continue
    
    if len(rows) == 1:
        print(f"[INFO] {nombre} solo tiene 1 entrada")
        # Verificar si está correcto
        if rows[0]['alternatives'] is not None:
            alt_count = len(rows[0]['alternatives'])
            print(f"  alternativas: {alt_count}")
        continue
    
    print(f"[INFO] {nombre} tiene {len(rows)} entradas, eliminando {len(rows)-1} duplicados")
    # Mantener el primero, eliminar el resto
    ids = sorted([r['id'] for r in rows])
    for cid in ids[1:]:
        db.run_query("DELETE FROM channels WHERE id = %s", (cid,))
        print(f"  Eliminado: {cid}")
    
    # Verificar el resultado
    rows = db.run_query("SELECT id, stream, alternatives FROM channels WHERE name = %s", (nombre,), fetchone=False)
    if rows:
        print(f"  Restante: id={rows[0]['id']}, alternatives={rows[0]['alternatives']}")

print("\nProceso completado. Ejecutar corregir_canales.py ahora.")
