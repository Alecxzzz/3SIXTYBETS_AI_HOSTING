"""Prueba offline de env_para_railway.py (sin panel, sin red, sin secretos).

Verifica lo unico que importa de verdad: que el bloque que se pega en el Raw
Editor de Railway llegue SIN comillas, SIN comentarios, ordenado, y que respete
lo que Railway no admite (PORT propio, valores multilinea).
"""

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_para_railway as m

fallos = []
tmpdir = tempfile.mkdtemp()
origen = os.path.join(tmpdir, "env.prueba")
destino = os.path.join(tmpdir, ".env.railway")

CONTENIDO = """\
# comentario de cabecera: NO debe llegar al panel
ALPHA=1
BETA="con espacio"
GAMMA='comillas simples'
DELTA=x#y
EPSILON="valor con # dentro"
ZETA=
PORT=8080
"""

with open(origen, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(CONTENIDO)


def check(nombre, condicion, detalle=""):
    print(("[OK]   " if condicion else "[FALLA] ") + nombre + (
        f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def capturar(func, *args, **kwargs):
    """Ejecuta func y devuelve (resultado, texto impreso)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        resultado = func(*args, **kwargs)
    return resultado, buf.getvalue()


# --- 1. Lectura del .env ------------------------------------------------
pares = m.cargar(origen)
check("lee las 7 variables (ignora el comentario)", len(pares) == 7,
      sorted(pares))
check("quita las comillas dobles", pares.get("BETA") == "con espacio",
      pares.get("BETA"))
check("quita las comillas simples", pares.get("GAMMA") == "comillas simples",
      pares.get("GAMMA"))
check("'#' pegado se conserva", pares.get("DELTA") == "x#y", pares.get("DELTA"))
check("'#' dentro de comillas se conserva",
      pares.get("EPSILON") == "valor con # dentro", pares.get("EPSILON"))
check("variable vacia se conserva", pares.get("ZETA") == "", pares.get("ZETA"))

# --- 2. Motivos para no pegar un valor ----------------------------------
check("multilinea se detecta", "lineas" in (m.motivo_no_pegable("a\nb") or ""),
      m.motivo_no_pegable("a\nb"))
check("espacios al borde se detectan",
      "espacios" in (m.motivo_no_pegable("valor  ") or ""),
      m.motivo_no_pegable("valor  "))
check("tabuladores se detectan",
      "tabuladores" in (m.motivo_no_pegable("a\tb") or ""),
      m.motivo_no_pegable("a\tb"))
check("un valor normal pasa", m.motivo_no_pegable("3SIXTYBETS <no-reply@x>") is None,
      m.motivo_no_pegable("3SIXTYBETS <no-reply@x>"))

# --- 3. Depuracion: PORT fuera, multilinea aparte -----------------------
bloque, problemas, ignoradas = m.depurar(pares)
check("PORT se descarta", ignoradas == ["PORT"], ignoradas)
check("PORT no esta en el bloque", "PORT" not in bloque, sorted(bloque))
check("quedan 5 pegables", len(bloque) == 5, sorted(bloque))
check("' #' se aparta (se cortaria como comentario)",
      [c for c, _ in problemas] == ["EPSILON"] and "EPSILON" not in bloque,
      problemas)

con_multi, problemas2, _ = m.depurar({"MYSQL_CA": "linea1\nlinea2", "OK": "1"})
check("la multilinea se aparta", "MYSQL_CA" not in con_multi, sorted(con_multi))
check("la multilinea se explica", [c for c, _ in problemas2] == ["MYSQL_CA"],
      problemas2)

# --- 4. Formato del bloque ----------------------------------------------
texto = m.bloque_railway(bloque)
lineas = texto.splitlines()
check("una linea por variable", len(lineas) == 5, len(lineas))
check("sin comentarios", not any(l.startswith("#") for l in lineas), lineas)
check("sin comillas", not any('"' in l or "'" in l for l in lineas), lineas)
claves = [l.split("=", 1)[0] for l in lineas]
check("claves ordenadas", claves == sorted(claves, key=str.lower), claves)
check("termina en salto de linea", texto.endswith("\n"), repr(texto[-3:]))

# --- 5. Variables imprescindibles --------------------------------------
faltan, plantillas, recomendadas = m.verificar(bloque)
check("detecta que faltan las obligatorias",
      {"ADMIN_USERNAME", "ADMIN_PASSWORD", "CHAT_CONV_SECRET",
       "FRONTEND_ORIGINS"} <= set(faltan), faltan)
check("detecta que falta MYSQL_HOST", "MYSQL_HOST" in faltan, faltan)
check("detecta PUBLIC_BASE_URL ausente", "PUBLIC_BASE_URL" in recomendadas,
      recomendadas)

completo = dict(bloque)
completo.update({
    "ADMIN_USERNAME": "alec",
    "ADMIN_PASSWORD": "secreto",
    "CHAT_CONV_SECRET": "un-string-largo",
    "FRONTEND_ORIGINS": "https://threesixtybets-chat.vercel.app",
    "MYSQL_HOST": "mysql-x.aivencloud.com",
    "MYSQL_PORT": "19392",
    "MYSQL_USER": "avnadmin",
    "MYSQL_PASSWORD": "pw",
    "MYSQL_DATABASE": "defaultdb",
    "PUBLIC_BASE_URL": "https://sixty.up.railway.app",
    "EMAIL_PROVIDER": "resend",
})
faltan2, plantillas2, recomendadas2 = m.verificar(completo)
check("sin faltas cuando esta todo", faltan2 == [], faltan2)
check("sin plantillas cuando esta todo", plantillas2 == [], plantillas2)

plantilla = {"MYSQL_URL": "mysql://x", "DEMIAN_AI_API_KEY": "sk-or-v1-tu_key_para_demian"}
faltan3, plantillas3, _ = m.verificar(plantilla)
check("MYSQL_URL sustituye a las sueltas",
      not any(c.startswith("MYSQL_") and c != "MYSQL_URL" for c in faltan3), faltan3)
check("detecta valor de plantilla", plantillas3 == ["DEMIAN_AI_API_KEY"],
      plantillas3)

# --- 6. Avisos que no rompen el deploy pero si el producto ---------------
avisos_viejos = m.avisos_extra({"PUBLIC_BASE_URL": "https://x.northflank.app"})
check("avisa de PUBLIC_BASE_URL vieja",
      any("host viejo" in a for a in avisos_viejos), avisos_viejos)
avisos_sin = m.avisos_extra({})
check("avisa si falta PUBLIC_BASE_URL",
      any("Falta PUBLIC_BASE_URL" in a for a in avisos_sin), avisos_sin)
check("avisa si EMAIL_PROVIDER=none",
      any("EMAIL_PROVIDER" in a for a in avisos_sin), avisos_sin)
avisos_ok = m.avisos_extra({"PUBLIC_BASE_URL": "https://sixty.up.railway.app",
                            "EMAIL_PROVIDER": "resend"})
check("sin avisos cuando esta bien", avisos_ok == [], avisos_ok)

# --- 7. Flujo completo de main() ---------------------------------------
_, salida = capturar(m.main, ["--origen", origen, "--salida", destino])
check("main escribe el fichero", os.path.exists(destino))
check("main informa cuantas variables pego", "OK: 5 variables" in salida, salida)
pegado = open(destino, encoding="utf-8").read()
lineas_pegadas = pegado.splitlines()
check("el fichero pegado no lleva comentarios",
      not any(l.startswith("#") for l in lineas_pegadas), lineas_pegadas)
check("el fichero pegado no lleva comillas",
      not any('"' in l or "'" in l for l in lineas_pegadas), lineas_pegadas)
check("el fichero pegado no lleva PORT", "PORT=" not in pegado, lineas_pegadas)
check("avisa de que PORT se descarto", "PORT" in salida and "descartadas" in salida,
      salida)

# --- 8. Round-trip: lo pegado se lee igual que el .env original ---------
try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None
    print("[SKIP] python-dotenv no instalado: se omite el round-trip")

if dotenv_values:
    releido = dotenv_values(destino)
    esperado = {c: v for c, v in bloque.items()}
    for clave, valor in esperado.items():
        check(f"Railway leeria {clave} igual", releido.get(clave) == valor,
              f"esperado {valor!r}, leido {releido.get(clave)!r}")

# --- 9. Errores claros --------------------------------------------------
try:
    m.main(["--origen", os.path.join(tmpdir, "no-existe.env"), "--salida", destino])
    check("origen inexistente aborta", False)
except SystemExit as exc:
    check("origen inexistente aborta", "No existe" in str(exc), str(exc))

cwd = os.getcwd()
vacio = tempfile.mkdtemp()
os.chdir(vacio)
try:
    try:
        m.main(["--salida", os.path.join(vacio, "salida")])
        check("sin .env en el directorio aborta", False)
    except SystemExit as exc:
        check("sin .env en el directorio aborta", "No encuentro" in str(exc), str(exc))
finally:
    os.chdir(cwd)

# --- 10. Prefiere .env.northflank sobre .env ----------------------------
doscopias = tempfile.mkdtemp()
with open(os.path.join(doscopias, ".env"), "w", encoding="utf-8") as fh:
    fh.write("SOLO_LOCAL=1\n")
with open(os.path.join(doscopias, ".env.northflank"), "w", encoding="utf-8") as fh:
    fh.write("SOLO_NF=1\n")
os.chdir(doscopias)
try:
    _, salida2 = capturar(m.main, ["--salida", os.path.join(doscopias, "out")])
    elegido = open(os.path.join(doscopias, "out"), encoding="utf-8").read()
finally:
    os.chdir(cwd)
check("prefiere .env.northflank (la fuente real de produccion)",
      "SOLO_NF=1" in elegido and "SOLO_LOCAL" not in elegido, elegido)
check("lo dice en pantalla", ".env.northflank" in salida2, salida2)

print()
if fallos:
    print(f"FALLARON {len(fallos)} comprobaciones: {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasaron.")

