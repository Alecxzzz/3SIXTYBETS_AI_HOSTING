# -*- coding: utf-8 -*-
"""
Reorganiza los canales de deportes del panel admin (tabla `channels`) segun lo
pedido, dejando en la BD SOLO los canales mencionados:

  1. Claro Sports 1 HD      -> opcion 2 = a0ey (el stream del "Claro Sports 1 SD",
                               que era el mismo canal duplicado: se borra).
  2. DSPORTS 1 (NUEVO)      -> opcion 1 = a00q (el de la lista del reproductor)
                               y opcion 2 = a0s8 (que vivia en "DIRECTV SPORTS").
  3. TUDN                   -> opcion 1 = a07z (antes vivia en "TUDN-IXP", que se
                               borra) y opcion 2 = ca039.
  4. FOX SPORT (a080)       -> pasa a llamarse "FOX DEPORTES (ESPAÑOL)".
  5. FOX SPORT 2-IXP (a081) -> pasa a llamarse "FOX DEPORTES 2 ESPAÑOL".
  6. Se quedan intactos: SPOR TV 1 BR, SPOR TV 3 BR, SPORTV 2 BR.

Borrados: "Claro Sports 1 SD", "TUDN-IXP" y "DIRECTV SPORTS" (sus URLs siguen
vivas como opcion 2 de su canal principal, no se pierde ninguna fuente).

Seguridad:
- Antes de tocar nada guarda TODAS las filas en
  respaldo_canales_reorganizacion.json (permite reconstruir el estado anterior).
- Dry-run por defecto: solo muestra el plan. Con --apply lo ejecuta.

Uso:
    python reorganizar_canales_deportes.py            # dry-run
    python reorganizar_canales_deportes.py --apply    # aplica
"""

import json
import sys

import db


A0EY = "http://138.59.177.78:9999/play/a0ey/index.m3u8"
A0S8 = "http://181.224.255.210:8001/play/a0s8/index.m3u8"
A00Q = "http://38.252.215.33:8000/play/a00q/index.m3u8"
A07Z = "http://138.59.177.78:9999/play/a07z/index.m3u8"
CA039 = "http://200.115.120.1:8000/play/ca039/index.m3u8"

RESPALDO = "respaldo_canales_reorganizacion.json"

# (nombre_actual, nombre_nuevo|None, stream_nuevo|None, alternatives_nuevas|None)
CAMBIOS = [
    ("Claro Sports 1 HD", None, None, [A0EY]),
    ("FOX SPORT", "FOX DEPORTES (ESPAÑOL)", None, None),
    ("FOX SPORT 2-IXP", "FOX DEPORTES 2 ESPAÑOL", None, None),
    ("TUDN", None, A07Z, [CA039]),
]

# (nombre, stream, alternatives)
NUEVOS = [
    ("DSPORTS 1", A00Q, [A0S8]),
]

BORRAR = [
    "Claro Sports 1 SD",   # duplicado de Claro Sports 1 HD (a0ey pasa a opcion 2)
    "TUDN-IXP",            # a07z pasa a ser la opcion 1 de TUDN
    "DIRECTV SPORTS",      # a0s8 pasa a ser la opcion 2 de DSPORTS 1
]


def _serializable(row):
    """Fila de MySQL -> dict serializable a JSON (datetime -> ISO 8601)."""
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


def _por_nombre(rows):
    return {r["name"]: r for r in rows}


def _plan(rows):
    """Devuelve (plan, problemas). plan = lista de tuplas (accion, ...)."""
    actuales = _por_nombre(rows)
    problemas, plan = [], []

    for nombre, nombre_nuevo, stream_nuevo, alts in CAMBIOS:
        row = actuales.get(nombre)
        if not row:
            problemas.append(f"no existe el canal {nombre!r} (nada que actualizar)")
            print(f"  [AVISO] {nombre!r} no esta en la BD: se salta")
            continue
        destino = nombre_nuevo or nombre
        if nombre_nuevo and nombre_nuevo in actuales:
            problemas.append(f"{nombre_nuevo!r} ya existe (no se renombra {nombre!r})")
            print(f"  [AVISO] {nombre_nuevo!r} ya existe: se salta el renombrado de {nombre!r}")
            continue
        plan.append(("UPDATE", nombre, destino, stream_nuevo, alts))

    for nombre, stream, alts in NUEVOS:
        if nombre in actuales:
            problemas.append(f"{nombre!r} ya existe (no se agrega)")
            print(f"  [AVISO] {nombre!r} ya existe: se salta el alta")
            continue
        plan.append(("INSERT", None, nombre, stream, alts))

    for nombre in BORRAR:
        if nombre not in actuales:
            print(f"  [AVISO] {nombre!r} no esta en la BD: nada que borrar")
            continue
        plan.append(("DELETE", nombre, None, None, None))

    return plan, problemas


def _aplicar(accion, nombre, destino, stream, alts, actuales):
    """Ejecuta un cambio contra la BD. Devuelve True si salio bien."""
    if accion == "UPDATE":
        sets, params = ["name = %s"], [destino]
        if stream:
            sets.append("stream = %s")
            params.append(stream)
        if alts is not None:
            sets.append("alternatives = %s")
            params.append(json.dumps(alts))
        sets.append("updated_at = %s")
        params.extend([db.now_utc(), actuales[nombre]["id"]])
        return bool(db.run_query(
            f"update channels set {', '.join(sets)} where id = %s", tuple(params)
        ))

    if accion == "INSERT":
        return bool(db.run_query(
            """
            insert into channels
            (id, name, stream, type, status, ads, use_proxy, geo_restriction,
             alternatives, created_at, updated_at)
            values (%s, %s, %s, 'm3u8', 'ACTIVO', false, true, 'NONE', %s, %s, %s)
            """,
            (db.secrets.token_urlsafe(8), destino, stream,
             json.dumps(alts or []), db.now_utc(), db.now_utc()),
        ))

    return bool(db.delete_channel(actuales[nombre]["id"]))


def main() -> int:
    apply = "--apply" in sys.argv

    rows = db.run_query("select * from channels order by name asc") or []
    actuales = _por_nombre(rows)

    print(f"Canales en la BD ahora mismo: {len(rows)}")
    print("-" * 96)

    plan, problemas = _plan(rows)

    if not plan:
        print("Nada que hacer: la BD ya esta como se pide.")
        return 1 if problemas else 0

    for accion, nombre, destino, stream, alts in plan:
        if accion == "UPDATE":
            print(f"  [UPDATE] {nombre} -> nombre={destino}"
                  + (f" | stream={stream}" if stream else "")
                  + (f" | opciones={alts}" if alts is not None else ""))
        elif accion == "INSERT":
            print(f"  [INSERT] {destino} | stream={stream} | opciones={alts}")
        else:
            print(f"  [DELETE] {nombre}")
    print("-" * 96)

    if not apply:
        print(f"DRY-RUN: {len(plan)} cambio(s) se aplicarian. Corre con --apply.")
        return 1 if problemas else 0

    with open(RESPALDO, "w", encoding="utf-8") as fh:
        json.dump([_serializable(r) for r in rows], fh, ensure_ascii=False, indent=2)
    print(f"Respaldo de {len(rows)} fila(s) guardado en {RESPALDO}")

    fallos = 0
    for accion, nombre, destino, stream, alts in plan:
        if not _aplicar(accion, nombre, destino, stream, alts, actuales):
            fallos += 1
            print(f"  [ERROR] fallo {accion} de {nombre or destino}")

    finales = db.run_query(
        "select name, stream, alternatives from channels order by name asc"
    ) or []
    print("-" * 96)
    print(f"RESULTADO: {len(plan) - fallos}/{len(plan)} cambio(s) aplicados. "
          f"Canales en la BD: {len(finales)}")
    for r in finales:
        print(f"  - {r['name']:<26} {r['stream']:<62} opciones={r.get('alternatives') or '[]'}")

    return 1 if (fallos or problemas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
