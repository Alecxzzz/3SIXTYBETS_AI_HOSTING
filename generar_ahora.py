"""Genera picks YA (bypass del gate de las 21:00) y resuelve pendientes.

Uso: .\\.venv\\Scripts\\python.exe generar_ahora.py
"""
import json

import dashboard


def main():
    stats = dashboard.generar_picks_dia(forzar=True)
    print("[generar_ahora] Picks generados:", json.dumps(stats, ensure_ascii=False))
    try:
        res = dashboard.resolver_picks_finalizados()
        print("[generar_ahora] Pendientes resueltos:", res)
    except Exception:
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
