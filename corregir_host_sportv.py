# -*- coding: utf-8 -*-
"""
Corrige el host muerto "fente.lista.luxe" -> "fonte.lista.luxe" en la tabla
`channels` (campo stream y alternatives).

Por que: "fente.lista.luxe" NO resuelve por DNS (es un typo de
"fonte.lista.luxe", que si resuelve y responde 200 application/x-mpegurl).
Los canales SPORTV de la BD apuntaban ahi: salian ACTIVO en el panel pero no
reproducen (ConnectionError en el navegador).

Uso:
    python corregir_host_sportv.py            # dry-run (solo muestra)
    python corregir_host_sportv.py --apply    # aplica
"""

import json
import sys

import db


MALO = "fente.lista.luxe"
BUENO = "fonte.lista.luxe"
RESPALDO = "respaldo_canales_host.json"


def _serializable(row):
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


def main() -> int:
    apply = "--apply" in sys.argv

    rows = db.run_query("select * from channels order by name asc") or []
    cambios = []
    for row in rows:
        stream = row.get("stream") or ""
        alts = row.get("alternatives") or ""
        if MALO in stream or MALO in alts:
            cambios.append(row)

    print(f"Canales con {MALO!r}: {len(cambios)}")
    print("-" * 96)
    for row in cambios:
        print(f"  {row['name']}")
        print(f"    stream : {row.get('stream')}")
        if row.get("alternatives"):
            print(f"    opciones: {row['alternatives']}")
        print(f"    -> {BUENO}")
    print("-" * 96)

    if not cambios:
        print("Nada que corregir.")
        return 0

    if not apply:
        print(f"DRY-RUN: {len(cambios)} canal(es) se corregirian. Corre con --apply.")
        return 0

    with open(RESPALDO, "w", encoding="utf-8") as fh:
        json.dump([_serializable(r) for r in cambios], fh, ensure_ascii=False, indent=2)
    print(f"Respaldo de {len(cambios)} fila(s) en {RESPALDO}")

    fallos = 0
    for row in cambios:
        ok = db.run_query(
            "update channels set stream = %s, alternatives = %s, updated_at = %s "
            "where id = %s",
            (
                (row.get("stream") or "").replace(MALO, BUENO),
                (row.get("alternatives") or "").replace(MALO, BUENO) or None,
                db.now_utc(),
                row["id"],
            ),
        )
        if not ok:
            fallos += 1
            print(f"  [ERROR] no se pudo corregir {row['name']}")

    print(f"LISTO: {len(cambios) - fallos}/{len(cambios)} canal(es) corregidos "
          f"a {BUENO}.")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
