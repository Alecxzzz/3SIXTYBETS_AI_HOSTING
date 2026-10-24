#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificar estado de picks de ayer en la base de datos de Aiven.

Nota: las credenciales deben leerse del archivo .env (git-ignored).
"""
import os

import pymysql
from dotenv import load_dotenv

# Cargar credenciales del .env (NO versionar secretos)
load_dotenv()

conn = pymysql.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 19392)),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE', 'defaultdb'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

with conn.cursor() as cur:
    cur.execute(
        "SELECT id, home_name, away_name, market, result "
        "FROM ai_picks WHERE DATE(created_at) = '2026-10-23' AND result IN ('FALLO', 'ACIERTO') "
        "ORDER BY result, id"
    )
    rows = cur.fetchall()

print(f"Picks resueltos ({len(rows)}):")
for r in rows:
    print(f"  [{r['result']:9}] ID={r['id']} | {r['home_name']} vs {r['away_name']} | {r['market'][:55]}")

conn.close()
