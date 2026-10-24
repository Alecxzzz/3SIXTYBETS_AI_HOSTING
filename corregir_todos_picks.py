#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
corregir_todos_picks.py

Corrige TODOS los picks mal clasificados de ayer (2026-10-23)
en la tabla ai_picks de tu MySQL (Aiven).

Correcciones:
- 7 picks que estaban FALLO y deberían ser ACIERTO (verdes)
- 2 picks que estaban ACIERTO y deberían ser FALLO (rojas)
- 2 picks adicionales que pediste específicamente (Torino -> FALLO, Brest/Nice -> ACIERTO)

Ejecuta desde tu PC donde tengas acceso a la DB de Aiven.
"""

import os
import sys

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


def actualizar_pick(cur, home, away, market, nuevo_result, fecha="2026-10-23"):
    """
    Actualiza un pick por partido + mercado + fecha.
    Devuelve cuántas filas se modificaron.
    """
    cur.execute(
        """
        UPDATE ai_picks
        SET result = %s
        WHERE home_name = %s
          AND away_name = %s
          AND market = %s
          AND DATE(created_at) = %s
        """,
        (nuevo_result, home, away, market, fecha),
    )
    return cur.rowcount


def main():
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"ERROR conectando a MySQL: {exc}")
        print("Asegurate de tener MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE en .env")
        sys.exit(1)

    # Lista de (home, away, market, resultado_correcto)
    correcciones = [
        # --- VERDES que estaban mal como FALLO (cambiar a ACIERTO) ---
        ("Go Ahead Eagles", "Telstar", "Go Ahead Eagles total de goles", "ACIERTO"),
        ("Utah Jazz", "New Orleans Pelicans", "Utah Jazz Totales", "ACIERTO"),
        ("Orlando Magic", "Minnesota Timberwolves", "Orlando Magic Totales", "ACIERTO"),
        ("Indiana Pacers", "Dallas Mavericks", "Indiana Pacers Totales", "ACIERTO"),
        ("Golden State Warriors", "Memphis Grizzlies", "Golden State Warriors Totales", "ACIERTO"),
        ("Atlante", "Atlético de San Luis", "Atlante FC total de goles", "ACIERTO"),
        ("Charlotte Hornets", "Atlanta Hawks", "Charlotte Hornets Totales", "ACIERTO"),

        # --- ROJAS que estaban mal como ACIERTO (cambiar a FALLO) ---
        ("Los Angeles Lakers", "LA Clippers", "Los Angeles Clippers Totales", "FALLO"),
        ("Miami Heat", "Detroit Pistons", "Detroit Pistons Totales", "FALLO"),

        # --- Pedidos específicos ---
        ("Torino", "Monza", "Tiros de esquina 1x2", "FALLO"),
        ("Brest", "Nice", "2ª Mitad - hándicap", "ACIERTO"),
    ]

    try:
        with conn.cursor() as cur:
            print("=== CORRECCIÓN DE PICKS DE AYER (2026-10-23) ===\n")
            total_afectadas = 0

            for home, away, market, resultado in correcciones:
                filas = actualizar_pick(cur, home, away, market, resultado)
                total_afectadas += filas
                estado = "VERDE (ACIERTO)" if resultado == "ACIERTO" else "ROJA (FALLO)"
                print(f"[{estado}] {home} vs {away} | {market} -> {filas} fila(s) actualizada(s)")

            conn.commit()
            print(f"\n=== COMPLETADO ===")
            print(f"Total de filas actualizadas: {total_afectadas}")
            print("Cambios guardados en la base de datos.")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR ejecutando corrección: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()