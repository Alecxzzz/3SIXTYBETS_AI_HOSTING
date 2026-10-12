"""
Pone las fuentes barinaslive1 como stream PRINCIPAL (Opcion 1) de los canales
ESPN directamente en la BASE DE DATOS, que es lo que sirve /channels en vivo.

Por que este script:
- El frontend en vivo carga los canales desde GET /channels -> BD.
- La tabla `channels` NO tiene columna `alternatives`; solo `stream`.
- Este update deja `stream` = barinaslive1 (la Opcion 1 / "En uso").
- Las alternativas (Opcion 2, 3...) siguen viviendo en src/data/channels.js
  (merge del reproductor: los estaticos tienen prioridad). Si el build nuevo
  ya esta desplegado, ahi se muestran; si no, la BD al menos da la principal.

Uso (en el Shell de Northflank, donde ya estan MYSQL_* / DATABASE_URL):
    python update_espn_channels.py            # muestra los cambios (dry-run)
    python update_espn_channels.py --apply    # aplica los UPDATE
"""

import sys

import db


# nombre_exacto -> nueva URL principal (barinaslive1 = Opcion 1)
ESPN_PRINCIPAL = {
    "ESPN 1": "https://barinaslive1.boldmss.com/ESPN/ESPN.isml/ESPN.m3u8",
    "ESPN 2": "https://barinaslive1.boldmss.com/ESPN_2/ESPN_2.isml/ESPN_2.m3u8",
    "ESPN 3": "https://barinaslive1.boldmss.com/ESPN_3/ESPN_3.isml/ESPN_3.m3u8",
    "ESPN 4": "https://barinaslive1.boldmss.com/ESPN_4/ESPN_4.isml/ESPN_4.m3u8",
    "ESPN 5": "https://barinaslive1.boldmss.com/ESPN_5/ESPN_5.isml/ESPN_5.m3u8",
    "ESPN 6": "https://barinaslive1.boldmss.com/ESPN_6/ESPN_6.isml/ESPN_6.m3u8",
    "ESPN7": "https://barinaslive1.boldmss.com/ESPN_7/ESPN_7.isml/ESPN_7.m3u8",
}


def main() -> int:
    apply = "--apply" in sys.argv

    rows = db.run_query("select id, name, stream from channels order by name asc") or []
    by_name = {r["name"]: r for r in rows}

    print(f"{'CANAL':10} {'ESTADO':10} NUEVO STREAM (Opcion 1)")
    print("-" * 90)
    cambios = 0
    for name, new_stream in ESPN_PRINCIPAL.items():
        row = by_name.get(name)
        if not row:
            print(f"{name:10} {'NO EXISTE':10} (no hay fila con ese nombre exacto en la BD)")
            continue
        current = (row.get("stream") or "").strip()
        if current == new_stream:
            print(f"{name:10} {'OK':10} ya apunta a barinaslive1")
            continue
        print(f"{name:10} {'CAMBIA':10} {current}")
        print(f"{'':10} {'':10} -> {new_stream}")
        if apply:
            db.run_query(
                "update channels set stream = %s, updated_at = %s where id = %s",
                (new_stream, db.now_utc(), row["id"]),
            )
        cambios += 1

    print("-" * 90)
    if not apply:
        print(f"DRY-RUN: {cambios} canal(es) cambiarian. Corre con --apply para aplicar.")
    else:
        print(f"LISTO: {cambios} canal(es) actualizados a barinaslive1 (Opcion 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
