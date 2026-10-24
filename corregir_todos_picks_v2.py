#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
corregir_todos_picks_v2.py

Corrige TODOS los picks mal clasificados de ayer (2026-10-23).
Usa los IDs exactos de la base de datos para mayor precisión.

Correcciones:
- 5 picks que estaban FALLO -> deben ser ACIERTO (verdes reales)
- 2 picks que estaban ACIERTO -> deben ser FALLO (rojas reales)
(Torino ya está FALLO y Brest/Nice ya está ACIERTO, no necesitan cambio)

Ejecuta desde tu PC donde tengas acceso a la DB de Aiven.
"""

import os
import sys
import pymysql
import pymysql.cursors

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def get_connection():
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "sixtybets")

    return pymysql.connect(
        host=host,
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


# Correcciones: (id, nuevo_resultado, descripcion)
CORRECCIONES = [
    # --- ACIERTO (verde) - estaban FALLO, son verdes reales ---
    ("TxoVofoJtx4", "ACIERTO", "Utah Jazz vs New Orleans Pelicans - Jazz Totales Menos de 126.5 (120 pts)"),
    ("9TAWN3YQ8kQ", "ACIERTO", "Orlando Magic vs Minnesota TW - Magic Totales Menos de 123.5 (93 pts)"),
    ("P_ka8cUhv_w", "ACIERTO", "Indiana Pacers vs Dallas Mavs - Pacers Totales Menos de 129.5 (105 pts)"),
    ("G-xUMN0eP0w", "ACIERTO", "Golden State vs Memphis - Warriors Totales Menos de 126.5 (108 pts)"),
    ("hY5L0n-JRTs", "ACIERTO", "Charlotte Hornets vs Atlanta Hawks - Hornets Totales Menos de 118.5 (81 pts)"),

    # --- FALLO (rojo) - estaban ACIERTO, son rojas reales ---
    ("cLPplhpWNyw", "FALLO", "Los Angeles Lakers vs LA Clippers - Clippers Totales Menos de 114.5 (117 pts)"),
    ("4iXL68U-L9g", "FALLO", "Miami Heat vs Detroit Pistons - Pistons Totales Menos de 124.5 (128 pts)"),
]


def main():
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"ERROR conectando a MySQL: {exc}")
        print("Asegurate de tener MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE en .env")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            print("=== CORRECCIÓN DE PICKS DE AYER (2026-10-23) ===\n")
            total_actualizados = 0

            # Primero mostramos el estado actual
            print("Estado ACTUAL antes de corregir:")
            print("-" * 100)
            for pick_id, nuevo_resultado, desc in CORRECCIONES:
                cur.execute("SELECT id, home_name, away_name, market, result FROM ai_picks WHERE id = %s", (pick_id,))
                fila = cur.fetchone()
                if fila:
                    print(f"  ID: {fila['id']}")
                    print(f"  Partido: {fila['home_name']} vs {fila['away_name']}")
                    print(f"  Market: {fila['market']}")
                    print(f"  Result actual: {fila['result']}")
                    print(f"  -> Cambiar a: {nuevo_resultado}")
                    print()
                else:
                    print(f"  ID: {pick_id} -> NO ENCONTRADO en la BD")
                    print()

            # Ahora hacemos los updates
            print("\n" + "=" * 60)
            print("Aplicando correcciones...\n")
            for pick_id, nuevo_resultado, desc in CORRECCIONES:
                cur.execute(
                    "UPDATE ai_picks SET result = %s WHERE id = %s",
                    (nuevo_resultado, pick_id),
                )
                if cur.rowcount > 0:
                    total_actualizados += 1
                    print(f"  ✅ ID {pick_id}: actualizado a {nuevo_resultado} ({desc})")
                else:
                    print(f"  ⚠️  ID {pick_id}: no se actualizó (no existe o ya tiene ese valor)")

            conn.commit()
            print(f"\n=== COMPLETADO ===")
            print(f"Filas actualizadas: {total_actualizados} de {len(CORRECCIONES)}")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
