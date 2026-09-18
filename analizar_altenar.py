"""Analiza mercados y cuotas del evento Altenar guardado."""
import json

d = json.load(open("doradobet_evento.json", encoding="utf-8"))

odds = d.get("odds") or []
print("odds raiz: tipo", type(odds).__name__, "| entradas:", len(odds))
for o in odds[:5]:
    print("  ODD:", json.dumps(o, ensure_ascii=False)[:220])

print()
for m in d.get("markets") or []:
    nombre = (m.get("name") or "").lower()
    if any(k in nombre for k in ("resultado", "1x2", "total de goles", "ambos", "handicap")):
        print("MERCADO:", m.get("name"), "| id:", m.get("id"), "| oddIds:", (m.get("desktopOddIds") or [])[:8])
        print("   claves:", [k for k in m.keys() if k not in ("desktopOddIds", "mobileOddIds")])
        print()

