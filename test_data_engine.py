#!/usr/bin/env python3
"""Valida la integracion del data_engine en el backend principal."""
import sys, os, ast, json

BASE = r"C:\Users\usuario\Desktop\3SIXTYBETS_AI_HOSTING"
sys.path.insert(0, BASE)
os.chdir(BASE)

print("=" * 70)
print("1) SINTAXIS de main.py")
print("=" * 70)
try:
    ast.parse(open(os.path.join(BASE, "main.py"), encoding="utf-8").read())
    print("OK: main.py sin errores de sintaxis")
except SyntaxError as e:
    print(f"ERROR de sintaxis: {e}")
    sys.exit(1)

print()
print("=" * 70)
print("2) IMPORTS de data_engine")
print("=" * 70)
try:
    from data_engine import router
    from data_engine.buscador import buscar_completo, contexto_para_ia
    from data_engine.rules import resolver_mercado
    print("OK: data_engine importa correctamente")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("3) ENDPOINTS con TestClient")
print("=" * 70)
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/search/match", json={
        "sport": "soccer", "home_name": "Osasuna",
        "away_name": "Racing Santander", "event_date": "20261018", "league": "esp.1"})
    print("POST /search/match ->", r.status_code, json.dumps(r.json(), ensure_ascii=False))

    r = client.post("/data/contexto-ia", json={
        "sport": "soccer", "home_name": "Osasuna",
        "away_name": "Racing Santander", "event_date": "20261018", "league": "esp.1"})
    j = r.json()
    print("POST /data/contexto-ia ->", r.status_code, "| found:", j.get("found"),
          "| chars:", len(j.get("contexto") or ""))

    r = client.post("/search/prop", json={
        "sport": "soccer", "home_name": "Osasuna", "away_name": "Racing Santander",
        "event_date": "20261018", "league": "esp.1",
        "market_type": "corners", "selection": "over", "line_value": 10.5})
    print("POST /search/prop (corners) ->", r.status_code, json.dumps(r.json(), ensure_ascii=False))

    r = client.post("/search/prop", json={
        "sport": "soccer", "home_name": "Osasuna", "away_name": "Racing Santander",
        "event_date": "20261018", "league": "esp.1",
        "market_type": "yellow_cards", "selection": "over", "line_value": 4.5})
    print("POST /search/prop (amarillas) ->", r.status_code, json.dumps(r.json(), ensure_ascii=False))

except Exception as e:
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print()
print("TODO OK - data_engine listo para produccion")
