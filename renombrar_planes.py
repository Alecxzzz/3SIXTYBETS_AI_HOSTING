"""Renombra las membresias en los bundles JS (BASICO / PREMIUM / VIP)."""
import re
import shutil

NOMBRES = {"plan15": "BASICO", "plan30": "PREMIUM", "plan60": "VIP"}
FILES = [
    "dep.js",
    "dist/assets/index-CJ9FEvj0.js",
    "dist/assets/index-KkOo2M37.js",
]

for f in FILES:
    try:
        text = open(f, encoding="utf-8", errors="ignore").read()
    except FileNotFoundError:
        print(f, "NO EXISTE")
        continue
    orig = text
    for code, nombre in NOMBRES.items():
        patron = r"(label:`)[^`]*(`,code:`" + code + r"`)"
        text = re.sub(patron, r"\g<1>" + nombre + r"\g<2>", text)
    if orig != text:
        shutil.copy(f, f + ".bak")
        open(f, "w", encoding="utf-8", errors="ignore", newline="").write(text)
        print(f, "-> renombrado (backup .bak)")
    else:
        print(f, "-> SIN CAMBIOS (revisar patron)")
