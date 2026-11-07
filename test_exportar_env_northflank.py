"""Prueba offline de exportar_env_northflank.py (sin token ni red real).

Simula las respuestas de la API de Northflank para verificar el flujo completo:
listado de servicios, merge servicio+secret groups, formato .env y diff contra
el .env local. No toca ningun recurso real.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exportar_env_northflank as m

LLAMADAS = []


def fake_get(cabeceras, ruta, params=None):
    LLAMADAS.append((ruta, params))
    if ruta.endswith("/services"):
        return {"data": {"services": [
            {"id": "svc-backend", "name": "backend", "serviceType": "combined"},
            {"id": "svc-otro", "name": "worker", "serviceType": "deployment"},
        ]}}
    if ruta.endswith("/runtime-environment"):
        return {"data": {"runtimeEnvironment": {
            "ALPHA": "1",
            "BETA": "con espacio",
            "GAMMA": "linea1\nlinea2",
            "DELTA": "",
            "ESPACIO_FINAL": "valor  ",
            "COMENTARIO": "con # comentario",
            "HASH_PEGADO": "x#y",
            "PLANTILLA": "abc${OTRA}",
        }}}
    if ruta.endswith("/secrets"):
        return {"data": {"secrets": [
            {"id": "grupo-app", "secretType": "environment"},
            {"id": "grupo-build", "secretType": "arguments"},
        ]}}
    if "/secrets/grupo-app" in ruta:
        return {"data": {"secrets": {"variables": {
            "ALPHA": "no-debe-ganar",
            "EPSILON": "x#y",
            "ZETA": 'tiene "comillas"',
        }}}}
    raise SystemExit(f"ruta inesperada: {ruta}")


m._get = fake_get
tmp = os.path.join(tempfile.mkdtemp(), ".env.prueba")

fallos = []


def check(nombre, condicion, detalle=""):
    print(("[OK]   " if condicion else "[FALLA] ") + nombre + (
        f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


# --- 1. Sin token: debe abortar con mensaje claro -------------------------
sys.argv = ["x", "--project", "p"]
try:
    m.main()
    check("sin token aborta", False)
except SystemExit as exc:
    check("sin token aborta", "token" in str(exc).lower(), str(exc))

# --- 2. Varios servicios sin --service: debe exigir --service -------------
sys.argv = ["x", "--token", "t", "--project", "p", "--salida", tmp]
try:
    m.main()
    check("exige --service cuando hay varios", False)
except SystemExit as exc:
    check("exige --service cuando hay varios", "--service" in str(exc), str(exc))

# --- 3. Flujo completo con --service -------------------------------------
sys.argv = ["x", "--token", "t", "--project", "p", "--service", "backend",
            "--salida", tmp]
m.main()

contenido = open(tmp, encoding="utf-8").read()
lineas = {l.split("=", 1)[0]: l.split("=", 1)[1]
          for l in contenido.splitlines()
          if l and not l.startswith("#")}

check("escribe el fichero", os.path.exists(tmp))
check("lee las 8 del servicio + 2 nuevas del grupo", len(lineas) == 10,
      f"hay {len(lineas)}: {sorted(lineas)}")
check("el servicio gana sobre el secret group", lineas.get("ALPHA") == "1",
      lineas.get("ALPHA"))
check("variable solo del grupo se agrega", lineas.get("EPSILON") == "x#y",
      lineas.get("EPSILON"))
check("espacios normales NO se citan (panel web)", lineas.get("BETA") == "con espacio",
      lineas.get("BETA"))
check("# pegado no se cita", lineas.get("HASH_PEGADO") == "x#y",
      lineas.get("HASH_PEGADO"))
check("' #' si se cita", lineas.get("COMENTARIO") == '"con # comentario"',
      lineas.get("COMENTARIO"))
check("espacios finales se citan", lineas.get("ESPACIO_FINAL") == '"valor  "',
      lineas.get("ESPACIO_FINAL"))
check("salto de linea escapado", lineas.get("GAMMA") == '"linea1\\nlinea2"',
      lineas.get("GAMMA"))
check("comillas internas escapadas", lineas.get("ZETA") == '"tiene \\"comillas\\""',
      lineas.get("ZETA"))
check("variable vacia se conserva", lineas.get("DELTA") == "", lineas.get("DELTA"))
check("secret group de build args no se consulta",
      all("grupo-build" not in r for r, _ in LLAMADAS))
check("pide show=this al leer un grupo",
      any(p and p.get("show") == "this" for _, p in LLAMADAS))

# --- 4. El .env generado se puede volver a leer --------------------------
releido = m.leer_env_local(tmp)
check("round-trip del .env", releido.get("ALPHA") == "1", releido.get("ALPHA"))

# --- 5. Formato de claves ordenado alfabeticamente ----------------------
claves = [l.split("=", 1)[0] for l in contenido.splitlines()
          if l and not l.startswith("#")]
check("claves ordenadas", claves == sorted(claves, key=str.lower), claves)

# --- 6. Parseo real con python-dotenv (el que usa el proyecto) -----------
try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None
    print("[SKIP] python-dotenv no instalado: se omite el parseo real")

if dotenv_values:
    real = dotenv_values(tmp)
    esperado = {
        "ALPHA": "1",
        "BETA": "con espacio",
        "COMENTARIO": "con # comentario",
        "ESPACIO_FINAL": "valor  ",
        "HASH_PEGADO": "x#y",
        "ZETA": 'tiene "comillas"',
        "GAMMA": "linea1\nlinea2",
        "DELTA": "",
    }
    for clave, valor in esperado.items():
        check(f"dotenv lee {clave} igual", real.get(clave) == valor,
              f"esperado {valor!r}, leido {real.get(clave)!r}")

print()
if fallos:
    print(f"FALLARON {len(fallos)} comprobaciones: {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasaron.")
