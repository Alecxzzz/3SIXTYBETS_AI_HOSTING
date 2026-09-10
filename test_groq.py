import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

import db  # carga .env
from ai.ia36 import GROQ_API_KEY, GROQ_URL, MODELO_DEFAULT, MODELO_FALLBACK

HDRS = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}


def chat(modelo: str, usar_tools: bool = False):
    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": "Responde breve en espanol."},
            {"role": "user", "content": "Dime en una linea: que es el over 2.5 en futbol."},
        ],
        "max_tokens": 300,
    }
    if usar_tools:
        payload["tools"] = [{
            "type": "function",
            "function": {
                "name": "buscar_web",
                "description": "Busca contexto en internet",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            },
        }]
        payload["tool_choice"] = "auto"
    t0 = time.time()
    r = requests.post(GROQ_URL, headers=HDRS, json=payload, timeout=60)
    dt = time.time() - t0
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:120]}", dt
    d = r.json()
    msg = d["choices"][0]["message"]
    txt = (msg.get("content") or "").strip()
    tools_usadas = [tc["function"]["name"] for tc in msg.get("tool_calls") or []]
    return f"OK ({d.get('model')}) | {txt[:90]!r} | tools={tools_usadas or '-'}", dt


print("=== 1) MODELOS DISPONIBLES EN TU CUENTA ===", flush=True)
r = requests.get("https://api.groq.com/openai/v1/models", headers=HDRS, timeout=20)
if r.status_code == 200:
    ids = sorted(m["id"] for m in r.json().get("data", []))
    for i in ids:
        print(" -", i, flush=True)
else:
    print("ERROR listando:", r.status_code, r.text[:200], flush=True)

print("\n=== 2) TEST PRIMARIO:", MODELO_DEFAULT, "===", flush=True)
out, dt = chat(MODELO_DEFAULT)
print(f"[{dt:.1f}s] {out}", flush=True)

print("\n=== 3) TEST FALLBACK:", MODELO_FALLBACK, "===", flush=True)
out, dt = chat(MODELO_FALLBACK)
print(f"[{dt:.1f}s] {out}", flush=True)

print("\n=== 4) TEST FUNCTION-CALLING (agente 365AI):", MODELO_DEFAULT, "===", flush=True)
out, dt = chat(MODELO_DEFAULT, usar_tools=True)
print(f"[{dt:.1f}s] {out}", flush=True)

print("\n=== 5) COMPARACION reasoning_effort en", MODELO_DEFAULT, "===", flush=True)
for esfuerzo in (None, "low"):
    payload = {
        "model": MODELO_DEFAULT,
        "messages": [
            {"role": "system", "content": "Eres analista de apuestas. Responde en 2 lineas."},
            {"role": "user", "content": "Real Madrid vs Getafe en casa: conviene el over 2.5? Da tu pick."},
        ],
        "max_tokens": 700,
    }
    if esfuerzo:
        payload["reasoning_effort"] = esfuerzo
    t0 = time.time()
    r = requests.post(GROQ_URL, headers=HDRS, json=payload, timeout=60)
    dt = time.time() - t0
    if r.status_code != 200:
        print(f"[{dt:.1f}s] effort={esfuerzo} -> HTTP {r.status_code}: {r.text[:100]}", flush=True)
        continue
    d = r.json()
    u = d.get("usage", {})
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    ct = u.get("completion_tokens")
    print(f"[{dt:.1f}s] effort={esfuerzo} -> reasoning_tokens={rt} | completion_tokens={ct}", flush=True)


