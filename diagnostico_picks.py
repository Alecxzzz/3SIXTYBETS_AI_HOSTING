"""Diagnostico rapido de picks acertados recientes."""
import db
from datetime import datetime, timezone

rows = db.run_query(
    "select id, event_name, titulo, market, selection, odds, result, pick_date, "
    "created_at, event_date from ai_picks where result = 'ACIERTO' and "
    "created_at >= '2026-09-17' order by created_at asc"
)
print("ACIERTOS con created_at >= hoy:", len(rows or []))
for r in rows or []:
    ca = r["created_at"].replace(tzinfo=timezone.utc).astimezone(db.TZ_NICARAGUA)
    print(
        f"{r['id']} | creadoNIC {ca.strftime('%d %H:%M')} | {r['event_name']} | "
        f"{r['titulo']} | cuota {r['odds']} | evento {r['event_date']} | "
        f"mercado: {r['market']}"
    )

print()
print("=== BUSCANDO LOS PICKS DEL USUARIO (pendientes o aciertos) ===")
nombres = ["McNeil", "Juventus", "Sonny Gray", "Milwaukee"]
for n in nombres:
    rows2 = db.run_query(
        "select id, event_name, titulo, market, selection, odds, result, pick_date, "
        "created_at, event_date from ai_picks where event_name like %s or titulo like %s "
        "order by created_at desc limit 3",
        (f"%{n}%", f"%{n}%"),
    )
    for r in rows2 or []:
        ca = r["created_at"].replace(tzinfo=timezone.utc).astimezone(db.TZ_NICARAGUA)
        print(
            f"[{n}] {r['id']} | creadoNIC {ca.strftime('%d %H:%M')} | {r['event_name']} | "
            f"{r['titulo']} | cuota {r['odds']} | {r['result']} | evento {r['event_date']}"
        )
