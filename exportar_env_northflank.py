"""
Exporta las variables de entorno REALES de un servicio de Northflank a un .env.

Por que existe:
    Las variables viven en el panel de Northflank (secret groups y/o el runtime
    environment del servicio). Si migras solo el repo, el contenedor nuevo
    arranca sin keys: la app levanta, pero sin cuotas reales, sin chat y sin
    correos, y el fallo es silencioso. Este script las baja por la API y las
    escribe en formato .env, que Railway/Render/Fly/VPS aceptan tal cual.

Token:
    Northflank -> Account settings -> API tokens, con permisos:
      Project > Secrets > Read   y   Project > Services > General > Read
    Se pasa por NORTHFLANK_API_TOKEN o con --token.

Uso (PowerShell):
    $env:NORTHFLANK_API_TOKEN = "nf_xxx"
    python exportar_env_northflank.py --project mi-proyecto --listar
    python exportar_env_northflank.py --project mi-proyecto --service mi-servicio

Salida:
    .env.northflank  (esta en .gitignore; NO se sube al repo)
"""

import argparse
import os
import sys

import requests

API = "https://api.northflank.com/v1"
DESTINO_POR_DEFECTO = ".env.northflank"
TIMEOUT = 30


def _get(cabeceras, ruta, params=None):
    url = f"{API}{ruta}"
    try:
        r = requests.get(url, headers=cabeceras, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SystemExit(f"[ERROR] No se pudo conectar con la API: {exc}")
    if r.status_code == 401:
        raise SystemExit("[ERROR] Token invalido o expirado (401).")
    if r.status_code == 403:
        raise SystemExit(
            "[ERROR] El token no tiene permisos de lectura (403). "
            "Necesita Project > Secrets > Read y Services > General > Read."
        )
    if r.status_code == 404:
        raise SystemExit(f"[ERROR] No encontrado (404): {ruta}")
    if r.status_code >= 400:
        raise SystemExit(f"[ERROR] {r.status_code} en {ruta}: {r.text[:300]}")
    return r.json()


def listar_servicios(cabeceras, proyecto):
    data = _get(cabeceras, f"/projects/{proyecto}/services", {"per_page": 100})
    servicios = (data.get("data") or {}).get("services") or []
    if not servicios:
        raise SystemExit(f"[ERROR] El proyecto '{proyecto}' no tiene servicios.")
    print(f"Servicios del proyecto '{proyecto}':")
    for s in servicios:
        print(f"  - id={s.get('id')!r:38} nombre={s.get('name')!r} "
              f"tipo={s.get('serviceType') or s.get('type') or '?'}")
    return servicios


def elegir_servicio(cabeceras, proyecto, pedido):
    servicios = listar_servicios(cabeceras, proyecto)
    if not pedido:
        if len(servicios) == 1:
            return servicios[0]["id"]
        raise SystemExit(
            "\n[ERROR] El proyecto tiene varios servicios: repite con "
            "--service <id> (usa --listar para verlos)."
        )
    for s in servicios:
        if pedido in (s.get("id"), s.get("name")):
            return s["id"]
    raise SystemExit(f"[ERROR] No hay ningun servicio con id/nombre '{pedido}'.")


def variables_del_servicio(cabeceras, proyecto, servicio_id):
    """Variables puestas directamente en el servicio (runtime environment).

    Intenta primero el endpoint dedicado (devuelve tambien lo heredado de los
    secret groups); si el plan o el permiso no lo permiten, cae al GET del
    servicio, que tambien las incluye.
    """
    try:
        data = _get(
            cabeceras,
            f"/projects/{proyecto}/services/{servicio_id}/runtime-environment",
            {"show": "all", "replaceTemplatedValues": "true"},
        )
        variables = (data.get("data") or {}).get("runtimeEnvironment") or {}
        if variables:
            return dict(variables)
    except SystemExit:
        pass
    data = _get(cabeceras, f"/projects/{proyecto}/services/{servicio_id}")
    info = data.get("data") or {}
    for contenedor in (info.get("deployment"), info):
        if isinstance(contenedor, dict):
            variables = contenedor.get("runtimeEnvironment") or {}
            if isinstance(variables, dict) and variables:
                return dict(variables)
    return {}


def variables_de_secretos(cabeceras, proyecto):
    """Todas las variables de los secret groups de tipo environment."""
    data = _get(cabeceras, f"/projects/{proyecto}/secrets", {"per_page": 100})
    grupos = (data.get("data") or {}).get("secrets") or []
    salida = {}
    for g in grupos:
        gid = g.get("id")
        if not gid:
            continue
        # 'arguments' son build args, no variables de ejecucion: se saltan.
        if g.get("secretType") == "arguments":
            print(f"  [omitido] {gid}: son build args, no variables de runtime")
            continue
        detalle = _get(cabeceras, f"/projects/{proyecto}/secrets/{gid}",
                       {"show": "this"})
        variables = (((detalle.get("data") or {}).get("secrets") or {})
                     .get("variables") or {})
        print(f"  [leido] secret group {gid!r}: {len(variables)} variables")
        for clave, valor in variables.items():
            salida.setdefault(clave, valor)
    return salida


def _formatear(clave, valor):
    texto = "" if valor is None else str(valor)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    multilinea = "\n" in texto
    # Comillas SOLO cuando hacen falta. Comprobado contra python-dotenv:
    #   - "A=x #y" sin comillas -> se lee "x"  (el " #" arranca un comentario)
    #   - "A=valor  " sin comillas -> pierde los espacios finales
    #   - un valor normal con espacios ("3SIXTYBETS <no-reply@x>") NO necesita
    #     comillas, y asi el panel web del host nuevo lo guarda tal cual.
    cita = (
        multilinea
        or texto != texto.strip()
        or any(c in texto for c in '"\\\t')
        or " #" in texto
        or texto.startswith(("#", "'", '"'))
    )
    if cita:
        # Dentro de comillas dotenv interpreta \\ y \": hay que escaparlos.
        texto = texto.replace("\\", "\\\\")
        if multilinea:
            texto = texto.replace("\n", "\\n")
        texto = texto.replace('"', '\\"')
        texto = f'"{texto}"'
    return f"{clave}={texto}"


def escribir_env(pares, destino):
    lineas = [
        "# Variables exportadas desde Northflank por exportar_env_northflank.py",
        "# Pegar tal cual en el host nuevo (Railway/Render/Fly) o usar como .env",
        "# en un VPS. Contiene SECRETOS: no se sube al repo.",
        "",
    ]
    for clave in sorted(pares, key=str.lower):
        lineas.append(_formatear(clave, pares[clave]))
    with open(destino, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lineas) + "\n")


def leer_env_local(ruta=".env"):
    pares = {}
    if not os.path.exists(ruta):
        return pares
    with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            pares[clave.strip()] = valor.strip().strip('"').strip("'")
    return pares

def main():
    parser = argparse.ArgumentParser(
        description="Exporta las variables de entorno de Northflank a un .env")
    parser.add_argument("--token", default=os.getenv("NORTHFLANK_API_TOKEN", ""))
    parser.add_argument("--project", default=os.getenv("NORTHFLANK_PROJECT", ""))
    parser.add_argument("--service", default="",
                        help="id o nombre del servicio (si hay varios)")
    parser.add_argument("--listar", action="store_true",
                        help="solo lista los servicios del proyecto")
    parser.add_argument("--sin-secretos", action="store_true",
                        help="no incluir las variables de los secret groups")
    parser.add_argument("--salida", default=DESTINO_POR_DEFECTO)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit(
            "[ERROR] Falta el token. Define NORTHFLANK_API_TOKEN o usa --token. "
            "Se crea en Northflank -> Account settings -> API tokens."
        )
    if not args.project:
        raise SystemExit(
            "[ERROR] Falta el proyecto. Usa --project <id> o define "
            "NORTHFLANK_PROJECT. El id sale en la URL del panel, por ejemplo "
            "https://app.northflank.com/project/mi-proyecto/settings"
        )

    cabeceras = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
    }

    if args.listar:
        listar_servicios(cabeceras, args.project)
        return

    servicio = elegir_servicio(cabeceras, args.project, args.service)
    print(f"\nLeyendo variables del servicio '{servicio}'...")
    pares = variables_del_servicio(cabeceras, args.project, servicio)
    print(f"  runtime environment del servicio: {len(pares)} variables")

    if not args.sin_secretos:
        print("Leyendo secret groups del proyecto...")
        secretos = variables_de_secretos(cabeceras, args.project)
        choques = sorted(set(secretos) & set(pares))
        if choques:
            print(f"  [aviso] {len(choques)} variables estan en el grupo y en el "
                  f"servicio; se queda el valor del servicio: {', '.join(choques)}")
        for clave, valor in secretos.items():
            pares.setdefault(clave, valor)

    if not pares:
        raise SystemExit(
            "[ERROR] No se obtuvo ninguna variable. Revisa que --service sea el "
            "correcto y que el token tenga permiso sobre los secret groups."
        )

    if os.path.exists(args.salida):
        print(f"\n[aviso] {args.salida} ya existia: se sobrescribe.")
    escribir_env(pares, args.salida)

    vacias = [k for k, v in pares.items() if not str(v or "").strip()]
    plantillas = [k for k, v in pares.items() if "${" in str(v or "")]
    print(f"\nOK: {len(pares)} variables escritas en {args.salida}")
    if vacias:
        print(f"[aviso] {len(vacias)} estan vacias en Northflank: "
              f"{', '.join(vacias)}")
    if plantillas:
        print("[aviso] estas contienen ${...} y python-dotenv los expande al "
              f"cargar el .env (revisalas a mano): {', '.join(plantillas)}")

    local = leer_env_local(".env")
    if local:
        solo_nf = sorted(set(pares) - set(local), key=str.lower)
        solo_local = sorted(set(local) - set(pares), key=str.lower)
        if solo_nf:
            print("\nEstas existen en Northflank y NO en tu .env local "
                  "(las habrias perdido al migrar):")
            print("  " + ", ".join(solo_nf))
        if solo_local:
            print("\nEstas estan solo en tu .env local (revisa si siguen vigentes):")
            print("  " + ", ".join(solo_local))
        if not solo_nf and not solo_local:
            print("\nTu .env local y Northflank coinciden en nombres de variables.")
    else:
        print("\n[aviso] No hay .env local para comparar.")

    print(f"\nSiguiente paso: pega el contenido de {args.salida} en el panel del "
          "host nuevo.")


if __name__ == "__main__":
    sys.exit(main())

