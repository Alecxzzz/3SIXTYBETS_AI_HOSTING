#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
corregir_picks.py

Corrige picks específicos en la tabla ai_picks de tu MySQL (Aiven).
Identifica los registros por partido + mercado + fecha (zona Nicaragua).
"""

import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pymysql
import pymysql.cursors


def get_connection():
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "sixtybets")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


def main():
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"ERROR conectando a MySQL: {exc}")
        print("Asegurate de tener MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE en .env")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # 1) Torino vs Monza -> FALLO (rojo)
            cur.execute(
                """
                UPDATE ai_picks
                SET result = %s
                WHERE home_name = %s
                  AND away_name = %s
                  AND market = %s
                  AND DATE(created_at) = %s
                """,
                ("FALLO", "Torino", "Monza", "Tiros de esquina 1x2", "2026-10-23"),
            )
            filas_torino = cur.rowcount

            # 2) Brest vs Nice -> ACIERTO (verde)
            cur.execute(
                """
                UPDATE ai_picks
                SET result = %s
                WHERE home_name = %s
                  AND away_name = %s
                  AND market = %s
                  AND DATE(created_at) = %s
                """,
                ("ACIERTO", "Brest", "Nice", "2ª Mitad - hándicap", "2026-10-23"),
            )
            filas_nice = cur.rowcount

        conn.commit()
        print("=== CORRECCIÓN COMPLETADA ===")
        print(f"Torino vs Monza (Tiros de esquina 1x2): {filas_torino} fila(s) actualizada(s) -> FALLO")
        print(f"Brest vs Nice (2ª Mitad - hándicap):     {filas_nice} fila(s) actualizada(s) -> ACIERTO")
        print("Cambios guardados en la base de datos.")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR ejecutando corrección: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()