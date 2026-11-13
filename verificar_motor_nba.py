# -*- coding: utf-8 -*-
"""Comprueba en la BD que los picks de NBA salieron del motor y que la
probabilidad quedo registrada. Solo lectura."""
import sys

import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

filas = db.run_query(
    "select id, event_name, market, selection, odds, model, confidence, tier,"
    " rationale from ai_picks where sport = 'nba' and pick_date = curdate()"
    " order by created_at desc"
) or []

print(f"\nPICKS NBA DE HOY: {len(filas)}")
print("=" * 90)
for f in filas:
    print(f"\n[{f.get('model')}] {f.get('event_name')}")
    print(f"  mercado : {f.get('market')}")
    print(f"  seleccion: {f.get('selection')}  @ {f.get('odds')}  "
          f"conf {f.get('confidence')}  {f.get('tier')}")
    print(f"  motivo  : {str(f.get('rationale'))[:320]}")

print("\n" + "=" * 90)
probs = db.run_query(
    "select event_id, market, seleccion, prob, prob_low, prob_high, edge,"
    " cuota, mu_local, mu_visita, elo_local, elo_visita, n_sims,"
    " modelo_version, descartado, motivo, pick_id from ai_picks_prob"
    " order by creado desc limit 30"
) or []
print(f"REGISTROS DE MEDICION (ai_picks_prob): {len(probs)}")
print("=" * 90)
for p in probs:
    marca = "PICK " if not p.get("descartado") else "desc."
    print(f"{marca} ev={p.get('event_id')[:10]:<10} "
          f"prob={p.get('prob')} [{p.get('prob_low')}-{p.get('prob_high')}] "
          f"edge={p.get('edge')} cuota={p.get('cuota')} "
          f"elo={p.get('elo_local')}/{p.get('elo_visita')} "
          f"sims={p.get('n_sims')} {p.get('modelo_version')} "
          f"{p.get('motivo') or ''}")
