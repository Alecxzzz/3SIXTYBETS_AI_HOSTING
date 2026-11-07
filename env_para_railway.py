"""
Prepara las variables de entorno para el "Raw Editor" de Railway.

Por que existe:
    En el .env (y en el .env.northflank que baja exportar_env_northflank.py) hay
    comentarios y comillas. Railway NO interpreta nada de eso: en su Raw Editor
    cada linea es literalmente `CLAVE=valor`, asi que un
    `EMAIL_FROM="3SIXTYBETS <no-reply@x.com>"` se guarda CON las comillas y los
    correos salen con un remitente invalido; y una linea `# comentario` se
    intenta guardar como variable. Este script deja el bloque limpio, aparta lo
    que no se puede pegar y avisa de las variables imprescindibles que falten.

Uso (PowerShell):
    python env_para_railway.py                       # usa .env.northflank o .env
    python env_para_railway.py --origen .env --salida .env.railway

Salida:
    .env.railway, listo para pegar TAL CUAL en
    Railway -> servicio -> Variables -> Raw Editor.
    Esta en .gitignore: contiene secretos, NO se sube al repo.
"""

import argparse
import os
import sys

DESTINO_POR_DEFECTO = ".env.railway"
# Orden de busqueda: primero lo que baja de Northflank (es la fuente real de
# produccion), luego el .env local.
ORIGENES = (".env.northflank", ".env")

# Railway inyecta PORT (y las RAILWAY_*) en el contenedor y enruta el trafico a
# esa variable. Definirla a mano rompe el enrutado: "Application failed to
# respond". Por eso se descarta si viene en el .env.
IGNORAR = {"PORT"}

OBLIGATORIAS = (
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "CHAT_CONV_SECRET",
    "FRONTEND_ORIGINS",
)
MYSQL_SUELTAS = (
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DATABASE",
)

# Restos de plantilla que delatan una variable sin configurar de verdad.
FRAGMENTOS_PLANTILLA = (
    "tu_api_key",
    "tu_password",
    "tu_key",
    "cambia_esto",
    "opcional_otra_cuenta",
    "xxxx",
)

# Las APIs de IA y el correo no bloquean el arranque (el codigo degrada), pero
# sin ellas el producto no funciona: se avisan, no se falla.
RECOMENDADAS = (
    "PUBLIC_BASE_URL",
    "GROQ_API_KEY",
    "DEMIAN_AI_API_KEY",
    "AI36_OPENROUTER_API_KEY",
    "DASHBOARD_AI_API_KEY",
    "ANALISTA_AI_API_KEY",
    "EMAIL_PROVIDER",
)



def _parsear_simple(texto):
    """Parser minimo de .env (solo si python-dotenv no esta instalado)."""
    pares = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
            valor = valor[1:-1]
        pares[clave.strip()] = valor
    return pares


def cargar(ruta):
    """Lee el fichero .env con el MISMO parser que usa db.py (python-dotenv).

    Asi lo que se pega en Railway es exactamente lo que la app leeria del .env
    en local. Si python-dotenv no esta instalado, se usa el parser minimo.
    """
    if not os.path.exists(ruta):
        raise SystemExit(f"[ERROR] No existe el fichero '{ruta}'.")
    try:
        from dotenv import dotenv_values
    except ImportError:
        with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
            return _parsear_simple(fh.read())
    pares = {}
    for clave, valor in dotenv_values(ruta).items():
        pares[clave] = "" if valor is None else str(valor)
    return pares


def motivo_no_pegable(valor):
    """Devuelve por que un valor no sobrevive al Raw Editor, o None si vale.

    El Raw Editor guarda la linea tal cual, sin comillas ni escapes: cualquier
    cosa que dependa de comillas (varias lineas, espacios al borde, tabuladores)
    se guardaria corrupta, y es peor que no pegarla.
    """
    texto = "" if valor is None else str(valor)
    if "\n" in texto or "\r" in texto:
        return "tiene varias lineas (el Raw Editor solo admite una)"
    if "\t" in texto:
        return "contiene tabuladores"
    if texto != texto.strip():
        return "empieza o acaba en espacios (Railway los recorta)"
    # " #" arranca un comentario en cualquier parser tipo dotenv: comprobado,
    # `A=valor con # dentro` se lee como "valor con". Como aqui no se puede
    # citar (las comillas se guardarian literales), la variable se aparta.
    if " #" in texto or texto.startswith("#"):
        return "lleva ' #' y se cortaria como comentario"
    if texto[:1] in ('"', "'"):
        return "empieza por comillas (se guardarian como parte del valor)"
    if texto.endswith("\\"):
        return "acaba en barra invertida (se lee como continuacion de linea)"
    return None


def depurar(pares):
    """Separa lo pegable de lo que hay que tocar a mano.

    Devuelve (bloque, problemas, ignoradas):
      bloque    -> dict de variables listas para pegar
      problemas -> [(clave, motivo), ...] de las que se quedan fuera
      ignoradas -> claves descartadas a proposito (PORT)
    """
    bloque, problemas, ignoradas = {}, [], []
    for clave, valor in pares.items():
        if clave in IGNORAR:
            ignoradas.append(clave)
            continue
        motivo = motivo_no_pegable(valor)
        if motivo:
            problemas.append((clave, motivo))
            continue
        bloque[clave] = "" if valor is None else str(valor)
    return bloque, problemas, ignoradas


def bloque_railway(pares):
    """Texto final: una linea CLAVE=valor por variable, sin comillas ni comentarios."""
    return "".join(f"{clave}={pares[clave]}\n" for clave in sorted(pares, key=str.lower))


def verificar(pares):
    """Devuelve (faltan, plantillas, recomendadas_ausentes)."""
    faltan = [c for c in OBLIGATORIAS if not str(pares.get(c, "")).strip()]
    tiene_url = bool(str(pares.get("MYSQL_URL", "")).strip())
    if not tiene_url:
        faltan += [c for c in MYSQL_SUELTAS if not str(pares.get(c, "")).strip()]
    plantillas = []
    for clave, valor in pares.items():
        bajo = str(valor).lower()
        if any(fragmento in bajo for fragmento in FRAGMENTOS_PLANTILLA):
            plantillas.append(clave)
    recomendadas = [c for c in RECOMENDADAS if not str(pares.get(c, "")).strip()]
    return sorted(faltan), sorted(plantillas), recomendadas


def avisos_extra(pares):
    """Avisos de cosas que no rompen el deploy pero si el producto."""
    avisos = []
    base = str(pares.get("PUBLIC_BASE_URL", "")).strip()
    if not base:
        avisos.append(
            "Falta PUBLIC_BASE_URL: los enlaces de los correos (recuperacion de "
            "contrasena) se arman con la URL del request y detras del proxy de "
            "Railway llegan como http."
        )
    elif "northflank" in base or "code.run" in base:
        avisos.append(
            f"PUBLIC_BASE_URL sigue apuntando al host viejo ({base}): cambiala por "
            "el dominio de Railway (https://<servicio>.up.railway.app)."
        )
    elif not base.startswith("https://"):
        avisos.append(f"PUBLIC_BASE_URL deberia empezar por https:// (ahora: {base}).")

    origenes = str(pares.get("FRONTEND_ORIGINS", "")).strip()
    if "railway.app" in origenes:
        avisos.append(
            "FRONTEND_ORIGINS incluye un dominio de Railway: eso es la API, no el "
            "frontend. Deja ahi los dominios de Vercel / del sitio."
        )

    if str(pares.get("EMAIL_PROVIDER", "")).strip().lower() in ("", "none"):
        avisos.append(
            "EMAIL_PROVIDER vale 'none': la recuperacion de contrasena no envia "
            "correos (el enlace solo queda en el log del contenedor)."
        )
    return avisos


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepara el bloque de variables para el Raw Editor de Railway")
    parser.add_argument("--origen", default="",
                        help="fichero .env de entrada (por defecto: .env.northflank "
                             "si existe, si no .env)")
    parser.add_argument("--salida", default=DESTINO_POR_DEFECTO,
                        help=f"fichero de salida (por defecto {DESTINO_POR_DEFECTO})")
    args = parser.parse_args(argv)

    origen = args.origen
    if not origen:
        origen = next((ruta for ruta in ORIGENES if os.path.exists(ruta)), "")
        if not origen:
            raise SystemExit(
                "[ERROR] No encuentro .env.northflank ni .env. Usa --origen <ruta>.")
    print(f"Origen: {origen}")

    pares = cargar(origen)
    if not pares:
        raise SystemExit(f"[ERROR] '{origen}' no tiene ninguna variable.")

    bloque, problemas, ignoradas = depurar(pares)
    if not bloque:
        raise SystemExit(
            "[ERROR] Ninguna variable se puede pegar tal cual. Revisa el fichero.")

    if os.path.exists(args.salida):
        print(f"[aviso] {args.salida} ya existia: se sobrescribe.")
    with open(args.salida, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(bloque_railway(bloque))

    print(f"\nOK: {len(bloque)} variables escritas en {args.salida}")
    print("Pega el contenido TAL CUAL en Railway -> servicio -> Variables -> "
          "Raw Editor.")

    if ignoradas:
        print(f"\n[descartadas] {', '.join(sorted(ignoradas))}: Railway inyecta "
              "PORT y enruta el trafico a esa variable; si la fijas a mano el "
              "servicio deja de responder.")

    if problemas:
        print("\n[NO se pueden pegar, revisar a mano en el panel]")
        for clave, motivo in problemas:
            print(f"  - {clave}: {motivo}")

    faltan, plantillas, recomendadas = verificar(bloque)
    if faltan:
        print("\n[FALTAN variables obligatorias] " + ", ".join(faltan))
        print("  Sin ellas el backend arranca pero no hay login, ni chat, ni BD.")
    if plantillas:
        print("\n[PARECEN PLANTILLA del .env.example] " + ", ".join(plantillas))
        print("  Si vienen de Northflank es que nunca se configuraron de verdad.")
    if recomendadas:
        print("\n[sin definir (el producto degrada sin ellas)] "
              + ", ".join(recomendadas))

    for aviso in avisos_extra(bloque):
        print(f"\n[aviso] {aviso}")

    print("\nSiguiente paso: tras pegar las variables, define a mano en Railway "
          "PUBLIC_BASE_URL y FRONTEND_ORIGINS con el dominio NUEVO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

