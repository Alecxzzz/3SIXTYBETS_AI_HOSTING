"""
Backfill del numero de aprobacion (PG approval number) de Pagadito.

Recupera el "reference" de ordenes completadas que quedaron sin el numero
de aprobacion guardado (pagos procesados antes del fix): consulta get_status
con el token_trans almacenado en la DB y actualiza cada orden.

Uso (en un entorno con PAGADITO_UID / PAGADITO_WSK y acceso a la DB, ej.
Render Shell):
    python backfill_approvals.py
"""
import sys

import db
from pagadito_client import PagaditoClient


def main():
    pending = db.pagadito_orders_missing_reference() or []
    print(f"Ordenes completadas sin numero de aprobacion: {len(pending)}")
    if not pending:
        print("Nada por hacer.")
        return 0

    client = PagaditoClient()
    ok = 0
    for row in pending:
        ern, token_trans = row["ern"], row["token_trans"]
        try:
            status = client.get_status(token_trans)
        except Exception as exc:
            print(f"[ERROR] {ern}: {exc}")
            continue
        print(f"[PAGADITO] {ern} -> {status}")
        reference = status.get("reference") or ""
        if reference:
            db.set_pagadito_reference(ern, reference)
            ok += 1
            print(f"[OK] {ern}: approval number = {reference}")
        else:
            print(f"[WARN] {ern}: Pagadito no devolvio reference")
    print(f"Listo: {ok}/{len(pending)} actualizadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
