"""Corrige a FALLO los picks mal resueltos (verificados con datos reales)."""
import db

MAL_MARCADOS = {
    "rLcgON040Xk": "Jeff McNeil: Over 0.5 hits (salio 0 hits)",
    "o-xMBIn3APE": "Sonny Gray: Over 8.5 ponches (salieron 6)",
    "OUqCVXhrL8A": "NEC vs Juventus: Over 3.5 tarjetas (salieron 2)",
    "tthvR9ktdRI": "Brewers Over 4.5 carreras (anotaron 4)",
}

for pid, motivo in MAL_MARCADOS.items():
    ok = db.run_query(
        "update ai_picks set result = %s, updated_at = %s where id = %s",
        ("FALLO", db.now_utc(), pid),
    )
    print(pid, "-> FALLO:", bool(ok), "|", motivo)

rows = db.run_query(
    "select id, result from ai_picks where id in (%s,%s,%s,%s)",
    tuple(MAL_MARCADOS.keys()),
)
for r in rows or []:
    print("verificado:", r["id"], r["result"])
