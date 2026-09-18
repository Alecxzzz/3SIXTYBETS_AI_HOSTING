"""Prueba que /v1/research acepta cada nivel de razonamiento (research_effort)."""
import requests
import os

from dotenv import load_dotenv

load_dotenv()
key = os.getenv("YOU_API_KEY") or os.getenv("YDC_API_KEY")
url = "https://api.you.com/v1/research"
H = {"Content-Type": "application/json", "X-API-Key": key}

for esfuerzo in ["lite", "standard", "deep", "exhaustive"]:
    p = {
        "input": "Analiza el partido Bayern vs Union Berlin: forma reciente y pronostico breve",
        "research_effort": esfuerzo,
        "background": False,
    }
    try:
        r = requests.post(url, headers=H, json=p, timeout=180)
        if r.ok:
            d = r.json()
            out = d.get("output")
            texto = out.get("content") if isinstance(out, dict) else (d.get("answer") or str(d))
            print(f"{esfuerzo:12} -> OK | {len(texto or '')} chars | {(texto or '')[:80]}", flush=True)
        else:
            print(f"{esfuerzo:12} -> RECHAZADO {r.status_code}: {r.text[:150]}", flush=True)
    except Exception as e:
        print(f"{esfuerzo:12} -> ERROR {e}", flush=True)
