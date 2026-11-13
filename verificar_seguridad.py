# -*- coding: utf-8 -*-
"""Prueba de humo del cableado REAL de seguridad (importa main, no lo simula).

Verifica lo que los tests unitarios no pueden:
  * que `main` importa sin romperse con los cambios,
  * que la dependency de auth acepta cookie Y Bearer, y rechaza sin nada,
  * que la IP se toma del lado correcto de X-Forwarded-For,
  * que una sesion creada de verdad funciona de punta a punta.

No deja basura: borra la sesion que crea.
"""

import sys

import security

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class _Client:
    def __init__(self, host="10.0.0.1"):
        self.host = host


class _URL:
    scheme = "https"


class _Req:
    def __init__(self, headers=None, cookies=None, client="10.0.0.1"):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.cookies = cookies or {}
        self.client = _Client(client)
        self.url = _URL()


def main() -> int:
    errores = []

    print("1) importando main...")
    import main as app_main
    print("   OK: app importada")

    # --- IP con XFF falsificado ---
    req = _Req(headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"})
    ip = app_main._ip_del_cliente(req)
    print(f"2) IP con XFF falsificado: {ip}")
    if ip != "203.0.113.7":
        errores.append("la IP se toma del lado equivocado de X-Forwarded-For")

    # --- Sin credenciales -> 401 ---
    from fastapi import HTTPException
    try:
        app_main.get_current_user(_Req(), None)
        errores.append("get_current_user acepto una peticion SIN sesion")
    except HTTPException as exc:
        print(f"3) sin sesion -> {exc.status_code} (correcto)")

    # --- Sesion real creada y usada por Bearer y por cookie ---
    import db
    fila = db.run_query("select id, username from users limit 1", fetchone=True)
    if not fila:
        print("4) no hay usuarios en la BD: no se puede probar la sesion")
        return 1 if errores else 0

    token = db.create_session(fila["id"])
    try:
        por_bearer = app_main.get_current_user(
            _Req(headers={"Authorization": f"Bearer {token}"}), f"Bearer {token}"
        )
        print(f"4) Bearer -> usuario {por_bearer.get('username')}")

        por_cookie = app_main.get_current_user(
            _Req(cookies={security.COOKIE_NOMBRE: token}), None
        )
        print(f"5) cookie -> usuario {por_cookie.get('username')}")

        if por_bearer.get("id") != fila["id"] or por_cookie.get("id") != fila["id"]:
            errores.append("la sesion no devolvio el usuario correcto")

        # La tabla no debe contener el token en claro.
        guardado = db.run_query(
            "select token from sessions where user_id = %s", (fila["id"],)
        ) or []
        if any(r["token"] == token for r in guardado):
            errores.append("el token se guarda EN CLARO en la tabla sessions")
        else:
            print("6) el token NO esta en claro en la tabla sessions")

        # Token inventado -> rechazado
        try:
            app_main.get_current_user(
                _Req(headers={"Authorization": "Bearer inventado"}), "Bearer inventado"
            )
            errores.append("acepto un token inventado")
        except HTTPException:
            print("7) token inventado -> rechazado")
    finally:
        db.delete_session(token)

    print()
    if errores:
        for e in errores:
            print(f"FALLO: {e}")
        return 1
    print("TODO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
