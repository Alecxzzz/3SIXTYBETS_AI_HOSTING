# -*- coding: utf-8 -*-
"""Copia de seguridad LÓGICA de la base de datos (sin dependencias externas).

Guarda todas las tablas en un .json.gz con fecha, y borra las copias viejas.
Sirve como red de seguridad aparte de los snapshots del hosting:

    python backup_db.py                    # guarda en ./backups
    python backup_db.py --salida D:\\copias --dias 30

Programarlo a diario:
  * Railway: un servicio "cron" con el comando `python backup_db.py`.
    OJO: el disco del contenedor es efímero, así que con --salida apunta a un
    volumen (si lo hay) o sube el resultado a tu nube.
  * Windows: Programador de tareas -> diario -> `python C:\\ruta\\backup_db.py`.
  * Linux/VPS: `0 4 * * * cd /app && python backup_db.py >> /var/log/backup.log 2>&1`

Restaurar: los .json.gz son legibles con `gzip -d` y sirven para reinsertar
filas con cualquier cliente de MySQL (o el script restore_db.py).
"""
import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import db


def _tablas() -> list:
    filas = db.run_query("show tables") or []
    nombres = []
    for f in filas:
        if isinstance(f, dict):
            # MySQL devuelve la clave con el nombre de la tabla
            nombres.append(list(f.values())[0])
    return sorted(nombres)


def _serializable(valor):
    """Convierte datetime/Decimal/bytes en algo que JSON entienda."""
    if isinstance(valor, (datetime,)):
        return valor.isoformat()
    if isinstance(valor, bytes):
        return valor.hex()
    try:
        json.dumps(valor)
        return valor
    except TypeError:
        return str(valor)


def hacer_backup(salida: str, dias: int) -> str:
    os.makedirs(salida, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(salida, f"backup_{marca}.json.gz")

    tablas = _tablas()
    print(f"[backup] {len(tablas)} tablas -> {ruta}", flush=True)
    datos = {"_meta": {"fecha": datetime.now(timezone.utc).isoformat(),
                       "tablas": len(tablas)}, "tablas": {}}
    total = 0
    inicio = time.time()
    for tabla in tablas:
        try:
            filas = db.run_query(f"select * from `{tabla}`") or []
        except Exception as exc:  # noqa: BLE001
            print(f"[backup] no pude leer {tabla}: {exc}", flush=True)
            continue
        limpio = [{k: _serializable(v) for k, v in fila.items()} for fila in filas]
        datos["tablas"][tabla] = limpio
        total += len(limpio)
        print(f"  {tabla:26} {len(limpio):6} filas", flush=True)

    with gzip.open(ruta, "wt", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False)

    tam = os.path.getsize(ruta) / 1024
    print(f"[backup] OK {total} filas, {tam:.1f} KB en {time.time() - inicio:.1f}s",
          flush=True)

    # Rotación: borrar copias mas viejas que N dias
    if dias > 0:
        limite = time.time() - dias * 86400
        for nombre in os.listdir(salida):
            if not (nombre.startswith("backup_") and nombre.endswith(".json.gz")):
                continue
            p = os.path.join(salida, nombre)
            if os.path.getmtime(p) < limite:
                try:
                    os.remove(p)
                    print(f"[backup] borrada copia vieja {nombre}", flush=True)
                except OSError:
                    pass
    return ruta


def main() -> int:
    ap = argparse.ArgumentParser(description="Copia de seguridad logica de MySQL")
    ap.add_argument("--salida", default="backups", help="carpeta destino")
    ap.add_argument("--dias", type=int, default=14, help="dias de copias a conservar")
    args = ap.parse_args()
    try:
        ruta = hacer_backup(args.salida, args.dias)
    except Exception as exc:  # noqa: BLE001
        print(f"[backup] FALLO: {exc}", flush=True)
        return 1
    print(f"[backup] archivo: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
