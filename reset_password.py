# -*- coding: utf-8 -*-
"""Resetea la contrasena de un usuario. Es la via de rescate cuando la
recuperacion por email esta cerrada (PERMITIR_RECUPERACION=0).

Por que existe
--------------
Al cerrar `/auth/forgot`, `/auth/reset`, `/recuperar` y `/verificar-email`
(porque ninguna puede pedir sesion: el usuario que olvido su clave no la tiene),
un usuario que olvide la contrasena queda fuera para siempre. Esta herramienta
es la unica puerta: la ejecuta el administrador en el servidor, con acceso a la
base de datos.

Que hace, en orden:
  1. aplica la misma politica de contrasena que la web,
  2. cambia el hash,
  3. cierra TODAS las sesiones del usuario (si estaba comprometida, el token
     robado deja de servir),
  4. deja el evento en la auditoria.

Uso:
    python reset_password.py <usuario>
    python reset_password.py <usuario> --password "NuevaClave12345"
    python reset_password.py --todos              # resetea y CIERRA todo
"""

import argparse
import getpass
import sys

import db
import security

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Resetea una contrasena")
    ap.add_argument("usuario", nargs="?", help="nombre de usuario")
    ap.add_argument("--password", help="si se omite, se pide por teclado")
    ap.add_argument("--todos", action="store_true",
                    help="solo cierra TODAS las sesiones (no cambia claves)")
    args = ap.parse_args()

    if args.todos:
        db.borrar_todas_las_sesiones()
        security.evento("sesiones_cerradas", "ok", usuario="admin(cli)",
                        detalle="todas las sesiones cerradas desde la CLI")
        print("Todas las sesiones cerradas.")
        return 0

    if not args.usuario:
        ap.print_help()
        return 1

    usuario = db.get_user_by_username(args.usuario.strip())
    if not usuario:
        print(f"No existe el usuario {args.usuario!r}")
        return 1

    nueva = args.password or getpass.getpass("Contrasena nueva: ")
    if not args.password:
        repetida = getpass.getpass("Repite la contrasena: ")
        if nueva != repetida:
            print("No coinciden.")
            return 1

    ok, motivo = security.validar_password(nueva)
    if not ok:
        print(f"Rechazada: {motivo}")
        return 1

    if not db.set_password(usuario["id"], nueva):
        print("No se pudo guardar la contrasena.")
        return 1

    db.borrar_todas_las_sesiones(usuario["id"])
    security.evento("password_reseteada", "ok", usuario=usuario["username"],
                    detalle="reset por CLI (recuperacion por email cerrada)")
    print(f"Contrasena de {usuario['username']} cambiada. "
          f"Todas sus sesiones cerradas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
