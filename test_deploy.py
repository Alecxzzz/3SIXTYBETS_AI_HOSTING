import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

BASE = "https://site--threesixtybetssz--qytms2wflqbs.code.run"

print("=== POST /chat (modelo=groq, 'como vs leipzig', buscar=true) ===", flush=True)
t0 = time.time()
try:
    r = requests.post(
        f"{BASE}/chat",
        json={"mensaje": "como vs leipzig", "buscar": True, "modelo": "groq"},
        timeout=280,
    )
    print(f"status: {r.status_code} | tiempo: {time.time()-t0:.0f}s", flush=True)
    print((r.text or "")[:1500], flush=True)
except Exception as e:
    print(f"ERROR tras {time.time()-t0:.0f}s:", e, flush=True)
