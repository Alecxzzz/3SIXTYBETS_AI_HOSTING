# -*- coding: utf-8 -*-
"""AUDITORIA de resoluciones: por que un pick salio ACIERTO / FALLO / ANULADO.

Sin esta tabla no se puede demostrar nada: `ai_picks` guarda el resultado pero
no QUIEN lo decidio, con que marcador ni con que motivo. Cuando un pick sale
raro (o alguien pregunta "por que este fallo"), aqui esta la evidencia.

La tabla se crea sola la primera vez (`asegurar_tabla`), es idempotente y
`registrar` NUNCA lanza: una auditoria caida no puede tumbar el resolver.
"""
from __future__ import annotations

_TABLA_ASEGURADA = False

_DDL = """
create table if not exists ai_picks_audit (
    id bigint auto_increment primary key,
    pick_id varchar(64) not null,
    sport varchar(20) null,
    evento_id varchar(64) null,
    fuente varchar(20) not null,
    marcador varchar(120) null,
    veredicto varchar(12) null,
    motivo varchar(160) null,
    detalle text null,
    creado datetime not null,
    index ai_picks_audit_pick_idx (pick_id),
    index ai_picks_audit_fecha_idx (creado)
)
"""


def asegurar_tabla() -> bool:
    global _TABLA_ASEGURADA
    if _TABLA_ASEGURADA:
        return True
    try:
        import db

        ok = db.run_query(_DDL)
        _TABLA_ASEGURADA = bool(ok)
        return _TABLA_ASEGURADA
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] no se pudo crear ai_picks_audit: {exc}", flush=True)
        return False


def _marcador(detalle: dict) -> str:
    """Texto corto con el marcador que se uso para decidir."""
    try:
        equipos = (detalle or {}).get("teams") or []
        partes = []
        for e in equipos[:2]:
            partes.append(f"{e.get('name') or '?'} {e.get('score')}")
        return " | ".join(partes)[:120]
    except Exception:  # noqa: BLE001
        return ""


def registrar(pick: dict, veredicto, motivo: str = "", fuente: str = "espn",
              detalle: dict | None = None) -> None:
    """Guarda una linea de auditoria. Nunca lanza."""
    try:
        import json

        import db

        if not asegurar_tabla():
            return
        db.run_query(
            """
            insert into ai_picks_audit
                (pick_id, sport, evento_id, fuente, marcador, veredicto, motivo,
                 detalle, creado)
            values (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                str(pick.get("id") or "")[:64],
                str(pick.get("sport") or "")[:20],
                str(pick.get("eventId") or pick.get("event_id") or "")[:64],
                str(fuente)[:20],
                _marcador(detalle or {}),
                str(veredicto)[:12] if veredicto else None,
                str(motivo)[:160] if motivo else None,
                json.dumps(detalle or {}, ensure_ascii=False, default=str)[:60000],
            ),
        )
    except Exception as exc:  # noqa: BLE001 - la auditoria nunca rompe el flujo
        print(f"[audit] no se pudo registrar {pick.get('id')}: {exc}", flush=True)


__all__ = ["asegurar_tabla", "registrar"]
