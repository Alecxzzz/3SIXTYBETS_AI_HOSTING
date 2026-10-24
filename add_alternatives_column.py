"""
Script para añadir la columna 'alternatives' a la tabla 'channels' en la BD.
"""
import sys
sys.path.insert(0, ".")

import db

# Verificar si la columna alternatives ya existe
rows = db.run_query(
    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'channels' AND COLUMN_NAME = 'alternatives'"
)
print("Existe columna alternatives:", len(rows) > 0)
if len(rows) == 0:
    # Agregar la columna alternatives
    ok = db.run_query(
        "ALTER TABLE channels ADD COLUMN alternatives TEXT DEFAULT NULL"
    )
    if ok:
        print("Columna alternatives agregada correctamente")
    else:
        print("Error al agregar columna alternatives")
