#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analizar_picks_ayer.py

Consulta la tabla ai_picks de MySQL (Aiven) y lista los picks de AYER
clasificados: VERDE (ACIERTO), ROJA (FALLO), GRIS (PENDIENTE / ANULADO).

Requisitos:
  - python-dotenv, pymysql instalados (ya estan en requirements.txt del proyecto)
  - Ejecutar desde el directorio del proyecto para que cargue .env

Uso:
  python analizar_picks_ayer.py
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
        autocommit=True,
        connect_timeout=10,
    )


def fecha_ayer_nicaragua():
    tz_nic = timezone(timedelta(hours=-6))
    ahora_nic = datetime.now(tz_nic)
    ayer_nic = (ahora_nic - timedelta(days=1)).date()
    return ayer_nic


def main():
    ayer = fecha_ayer_nicaragua()
    print(f"\n=== ANALIZANDO PICKS DE AYER ({ayer}) ===\n")

    try:
        conn = get_connection()
    except Exception as exc:
        print(f"ERROR conectando a MySQL: {exc}")
        print("\nAsegurate de tener las variables de entorno configuradas:")
        print("  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, sport, sport_label, league, event_name,
                       market, selection, odds, result, verificado,
                       pick_date, created_at, updated_at, home_name, away_name
                FROM ai_picks
                WHERE pick_date = %s
                ORDER BY created_at ASC
                """,
                (ayer,),
            )
            rows = cur.fetchall() or []
    except Exception as exc:
        print(f"ERROR consultando ai_picks: {exc}")
        conn.close()
        sys.exit(1)

    if not rows:
        print(f"No se encontraron picks con pick_date = {ayer}.")
        print("\nNota: los picks se guardan con la fecha del pick (pick_date).")
        print("Si los picks de ayer fueron creados con otra fecha, ajustá el filtro.")
        conn.close()
        return

    verdes = []
    rojas = []
    grises = []

    for r in rows:
        resultado = (r.get("result") or "").upper()
        item = {
            "id": r.get("id"),
            "sport": r.get("sport"),
            "sport_label": r.get("sport_label"),
            "league": r.get("league"),
            "event_name": r.get("event_name"),
            "home_name": r.get("home_name"),
            "away_name": r.get("away_name"),
            "market": r.get("market"),
            "selection": r.get("selection"),
            "odds": r.get("odds"),
            "verificado": r.get("verificado"),
            "result": resultado,
            "created_at": str(r.get("created_at")) if r.get("created_at") else "-",
            "updated_at": str(r.get("updated_at")) if r.get("updated_at") else "-",
        }

        if resultado == "ACIERTO":
            verdes.append(item)
        elif resultado == "FALLO":
            rojas.append(item)
        else:
            grises.append(item)

    total = len(rows)
    n_verdes = len(verdes)
    n_rojas = len(rojas)
    n_grises = len(grises)
    pct_verdes = round(100 * n_verdes / (n_verdes + n_rojas), 1) if (n_verdes + n_rojas) > 0 else 0.0

    print(f"Total picks de ayer: {total}")
    print(f"  VERDES (ACIERTO):  {n_verdes}")
    print(f"  ROJAS (FALLO):     {n_rojas}")
    print(f"  GRIS (PENDIENTE/ANULADO): {n_grises}")
    if n_verdes + n_rojas > 0:
        print(f"  Efectividad (resueltos): {pct_verdes}%")
    print()

    if verdes:
        print("=" * 90)
        print(f"VERDES / ACIERTO ({n_verdes})")
        print("=" * 90)
        for v in verdes:
            odds_str = f"{v['odds']}" if v["odds"] else "N/D"
            verificado_str = f" [verificado x{v['verificado']}]" if v.get("verificado") else ""
            print(f"  [{v['created_at']}] {v['sport_label'] or v['sport']} | {v['league'] or '-'}")
            print(f"    Partido: {v['home_name'] or '-'} vs {v['away_name'] or '-'} | {v['event_name'] or '-'}")
            print(f"    Mercado: {v['market'] or '-'}")
            print(f"    PICK:    {v['selection'] or '-'}  (cuota {odds_str}){verificado_str}")
            print(f"    Resultado: ACIERTO")
            print()

    if rojas:
        print("=" * 90)
        print(f"ROJAS / FALLO ({n_rojas})")
        print("=" * 90)
        for r in rojas:
            odds_str = f"{r['odds']}" if r["odds"] else "N/D"
            verificado_str = f" [verificado x{r['verificado']}]" if r.get("verificado") else ""
            print(f"  [{r['created_at']}] {r['sport_label'] or r['sport']} | {r['league'] or '-'}")
            print(f"    Partido: {r['home_name'] or '-'} vs {r['away_name'] or '-'} | {r['event_name'] or '-'}")
            print(f"    Mercado: {r['market'] or '-'}")
            print(f"    PICK:    {r['selection'] or '-'}  (cuota {odds_str}){verificado_str}")
            print(f"    Resultado: FALLO")
            print()

    if grises:
        print("=" * 90)
        print(f"PENDIENTE / ANULADO ({n_grises})")
        print("=" * 90)
        for g in grises:
            odds_str = f"{g['odds']}" if g["odds"] else "N/D"
            print(f"  [{g['created_at']}] {g['sport_label'] or g['sport']} | {g['league'] or '-'}")
            print(f"    Partido: {g['home_name'] or '-'} vs {g['away_name'] or '-'} | {g['event_name'] or '-'}")
            print(f"    Mercado: {g['market'] or '-'}")
            print(f"    PICK:    {g['selection'] or '-'}  (cuota {odds_str})")
            print(f"    Resultado: {g['result']}")
            print()

    print("=" * 90)
    print("RESUMEN POR DEPORTE")
    print("=" * 90)
    por_deporte = {}
    for r in rows:
        sp = r.get("sport_label") or r.get("sport") or "?"
        d = por_deporte.setdefault(sp, {"total": 0, "aciertos": 0, "fallos": 0})
        d["total"] += 1
        res = (r.get("result") or "").upper()
        if res == "ACIERTO":
            d["aciertos"] += 1
        elif res == "FALLO":
            d["fallos"] += 1

    for sp, d in sorted(por_deporte.items()):
        resueltos = d["aciertos"] + d["fallos"]
        pct = round(100 * d["aciertos"] / resueltos, 1) if resueltos > 0 else 0.0
        print(f"  {sp}: {d['total']} picks | ACIERTO: {d['aciertos']} | FALLO: {d['fallos']} | Efectividad: {pct}%")

    print()
    conn.close()


if __name__ == "__main__":
    main()

