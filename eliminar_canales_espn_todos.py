"""
Borra de la tabla `channels` TODOS los canales cuyo NOMBRE contenga "ESPN"
y deja intactos los demas (CLARO SPORTS, DIRECTV SPORTS, FOX SPORT...).

Por que este script:
- El panel admin y la pagina de TV se alimentan de GET /admin/channels ->
  db.list_channels() -> tabla `channels` de MySQL. Borrar ahi es lo que hace
  desaparecer las tarjetas del panel (el boton "Borrar" hace exactamente eso,
  pero una por una).
- Se filtra por NOMBRE con LIKE '%ESPN%' en vez de una lista exacta de nombres:
  en la BD hay variantes con sufijos ("ESPN 1 IXP", "6 ESPN 4K_a0a0",
  "ESPN 5 Sur (ESPN07)", "ESPN2 HD", "ESPN6 HD"...). Una lista fija se queda
  corta y los canales "raros" siguen apareciendo. NO se mira la URL del stream:
  un canal que no se llame ESPN no se toca aunque su URL contenga "ESPN".

Seguridad:
- Antes de borrar guarda las filas eliminadas completas en
  respaldo_canales_espn.json (permite re-insertarlas si fue un error).
- Dry-run por defecto: muestra lo que borraria sin tocar nada.

Uso:
    python eliminar_canales_espn_todos.py            # dry-run (solo lista)
    python eliminar_canales_espn_todos.py --apply    # borra de verdad
"""

import json
import sys

import db


PATRON = "ESPN"
RESPALDO = "respaldo_canales_espn.json"


def _serializable(row):
    """Fila de MySQL -> dict serializable a JSON (datetime -> ISO 8601)."""
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


def main() -> int:
    apply = "--apply" in sys.argv

    rows = db.run_query(
        "select * from channels where upper(name) like %s order by name asc",
        (f"%{PATRON.upper()}%",),
    ) or []

    if not rows:
        print(f"No hay canales cuyo nombre contenga {PATRON!r}. Nada que borrar.")
        return 0

    print(f"Canales con {PATRON!r} en el nombre: {len(rows)}")
    print("-" * 96)
    for row in rows:
        print(f"  [{'BORRA' if apply else 'DRY-RUN'}] {row['name']:<38} id={row['id']}")
        print(f"            {row['stream']}")
    print("-" * 96)

    if not apply:
        print(f"DRY-RUN: {len(rows)} canal(es) se borrarian. "
              f"Corre con --apply para aplicar.")
        return 0

    with open(RESPALDO, "w", encoding="utf-8") as fh:
        json.dump([_serializable(r) for r in rows], fh, ensure_ascii=False, indent=2)
    print(f"Respaldo de {len(rows)} fila(s) guardado en {RESPALDO}")

    borrados, fallidos = 0, 0
    for row in rows:
        if db.delete_channel(row["id"]):
            borrados += 1
        else:
            fallidos += 1
            print(f"  [ERROR] no se pudo borrar {row['name']} (id={row['id']})")

    total = db.run_query("select count(*) as n from channels", fetchone=True) or {"n": 0}
    print(f"LISTO: {borrados} canal(es) ESPN borrados"
          + (f", {fallidos} fallido(s)" if fallidos else "")
          + f". Canales restantes en la BD: {total['n']}")
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
