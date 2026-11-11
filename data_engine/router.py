"""
Router FastAPI: motor de datos reales + verificacion de picks.
Se monta en main.py con app.include_router(data_engine.router).
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import hashlib

from data_engine.buscador import buscar_completo, contexto_para_ia
from data_engine.rules import resolver_mercado

router = APIRouter(tags=["data-engine"])


def _admin(authorization: Optional[str] = Header(None)):
    """Valida que el token pertenezca a un administrador."""
    import db as _db
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Necesitas iniciar sesion.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        user = _db.get_user_by_token(token)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Sesion invalida.")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acceso restringido a administradores.")
    return user


class MatchQuery(BaseModel):
    sport: str = "soccer"
    league: Optional[str] = None
    home_name: str
    away_name: str
    event_date: Optional[str] = None


class PropQuery(BaseModel):
    sport: str = "soccer"
    league: Optional[str] = None
    home_name: str
    away_name: str
    event_date: Optional[str] = None
    market_type: str
    player_name: Optional[str] = None
    selection: str
    line_value: Optional[float] = None
    user_id: Optional[str] = None


def _pid(q: PropQuery) -> str:
    base = f"{q.sport}|{q.home_name}|{q.away_name}|{q.market_type}|{q.player_name or ''}|{q.line_value or ''}"
    return hashlib.sha256(base.encode()).hexdigest()[:32]


def _inyectar_jugador(d, player_name: str):
    """Copia las stats del jugador a player_* para que el verificador las use."""
    for j in d.get("jugadores", []):
        if (j.get("nombre") or "").lower() == player_name.lower():
            for k, v in (j.get("stats") or {}).items():
                d[f"player_{k}"] = v
            d["player_team"] = j.get("equipo")
            return
    for j in d.get("leaders", []):
        if (j.get("nombre") or "").lower() == player_name.lower():
            d["player_team"] = j.get("equipo")


@router.post("/data/partido")
def data_partido(q: MatchQuery):
    """Datos COMPLETOS de un partido (marcador, stats, jugadores, eventos)."""
    d = buscar_completo(q.sport, q.home_name, q.away_name, q.event_date, q.league)
    if not d.get("found"):
        return {"found": False, "message": "Partido no encontrado en fuentes gratuitas."}
    d.pop("raw", None)
    return d


@router.post("/data/contexto-ia")
def data_contexto(q: MatchQuery):
    """Contexto en TEXTO listo para inyectar en la IA."""
    d = buscar_completo(q.sport, q.home_name, q.away_name, q.event_date, q.league)
    return {"found": d.get("found", False), "contexto": contexto_para_ia(d)}


@router.post("/search/match")
def search_match(q: MatchQuery):
    """Resumen del partido."""
    d = buscar_completo(q.sport, q.home_name, q.away_name, q.event_date, q.league)
    if not d.get("found"):
        return {"found": False, "source": None, "data": None}
    return {
        "found": True,
        "source": (d.get("fuentes_usadas") or [None])[0],
        "data": {
            "home_name": d.get("home_name"),
            "away_name": d.get("away_name"),
            "home_score": d.get("home_score"),
            "away_score": d.get("away_score"),
            "winner": d.get("winner"),
            "estado": d.get("estado"),
            "league": d.get("league"),
        },
    }


@router.post("/resolve/picks")
def resolve_picks(limite: int = 60, solo_hoy: bool = False, anular_no_resolubles: bool = False,
                  purgar_vencidos: bool = True, admin=Depends(_admin)):
    """
    Resuelve los picks PENDIENTE de la tabla ai_picks con el motor de datos reales.
    anular_no_resolubles=true marca ANULADO los que no se pueden verificar.
    purgar_vencidos=true (defecto) BORRA los picks que llevan mas de 72h sin
    poder verificarse: son los que hacian que el panel dijera siempre
    "0 resueltos / N sin datos" y que se acumularan para siempre (la misma
    politica que ya aplica dashboard.revisar_picks_hoy).

    INVARIANTE: solo se toca (resolver, anular o purgar) un pick cuyo partido YA
    TERMINO segun la fuente. Un partido en juego o programado jamas se anula.
    """
    import db as _db
    from data_engine.pick_parser import parsear_pick
    from datetime import datetime, timezone

    PURGA_HORAS = 72

    def _edad_horas(p: dict) -> float:
        """Horas desde el inicio del evento (-1 si la fecha no se entiende)."""
        try:
            f = p.get("event_date")
            dt = f if isinstance(f, datetime) else datetime.fromisoformat(str(f).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        except Exception:
            return -1

    # Solo partidos YA EMPEZADOS (evita resolver picks de partidos futuros);
    # que esten TERMINADOS se comprueba despues con el estado real de la fuente.
    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    where = "result='PENDIENTE' AND event_date < %s"
    params = [cutoff]
    if solo_hoy:
        where += " AND pick_date = CURDATE()"

    picks = _db.run_query(
        f"SELECT id, sport, league, home_name, away_name, event_date, market, titulo, selection "
        f"FROM ai_picks WHERE {where} ORDER BY event_date DESC LIMIT {int(limite)}",
        tuple(params)
    ) or []

    resueltos = 0
    sin_datos = 0
    anulados = 0
    en_juego = 0
    omitidos = 0
    purgados = 0
    detalles = []

    def _descartar(p: dict) -> None:
        """Suma a 'sin datos' o BORRA el pick si ya paso el plazo de purga."""
        nonlocal sin_datos, purgados
        if purgar_vencidos and _edad_horas(p) > PURGA_HORAS:
            try:
                if _db.delete_ai_pick(p["id"]):
                    purgados += 1
                    return
            except Exception as exc:
                print(f"[resolve/picks] no pude purgar {p.get('id')}: {exc}")
        sin_datos += 1

    for p in picks:
        mercado_txt = ((p.get("market") or "") + " " + (p.get("titulo") or "")).lower()
        if "primer juego" in mercado_txt or "primer punto" in mercado_txt:
            # Mercado PROHIBIDO (sin fuente que lo compruebe): no se intenta.
            # Antes se saltaba en silencio y el panel no cuadraba (32 revisados
            # = 3 + 10 y faltaban 19). Ahora se cuenta y, si es viejo, se borra.
            omitidos += 1
            if purgar_vencidos and _edad_horas(p) > PURGA_HORAS:
                try:
                    if _db.delete_ai_pick(p["id"]):
                        purgados += 1
                        omitidos -= 1
                except Exception as exc:
                    print(f"[resolve/picks] no pude purgar {p.get('id')}: {exc}")
            continue
        fecha = str(p.get("event_date") or "")[:10].replace("-", "") or None
        try:
            d = buscar_completo(p.get("sport") or "soccer", p.get("home_name") or "",
                                p.get("away_name") or "", fecha, p.get("league"))
            if not d.get("found") and fecha:
                # Intentar con fechas adyacentes (caso juegos que cruzan medianoche UTC/ET)
                try:
                    from datetime import datetime, timedelta
                    base = datetime.strptime(fecha, "%Y%m%d")
                    for delta in (-1, 1):
                        try:
                            f2 = (base + timedelta(days=delta)).strftime("%Y%m%d")
                            d2 = buscar_completo(p.get("sport") or "soccer", p.get("home_name") or "",
                                                p.get("away_name") or "", f2, p.get("league"))
                            if d2.get("found"):
                                d = d2
                                break
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception as e:
            print(f"[resolve/picks] error buscar {p.get('id')}: {e}")
            _descartar(p)
            continue

        if not d.get("found"):
            # Sin datos no se puede saber si el partido termino: se deja
            # PENDIENTE (nunca ANULADO por esta via). Si ya paso el plazo de
            # purga (72h) se borra para que no quede eterno.
            _descartar(p)
            continue

        # GUARDA DURA: solo un partido FINALIZADO admite veredicto o anulacion.
        estado = str(d.get("estado") or "").strip().lower()
        if estado in ("in", "pre"):
            en_juego += 1
            continue
        if not estado and (d.get("home_score") is None or d.get("away_score") is None):
            # Fuente sin estado y sin marcador: partido no terminado (o no
            # jugado). Manos fuera, sigue PENDIENTE.
            en_juego += 1
            continue

        parsed = parsear_pick(p)
        market_type, sel, linea = parsed if parsed else (None, None, None)
        if not market_type or not sel:
            # Mercado no interpretable. Solo se descarta si el partido ya
            # termino (arriba); si no, se espera a que se juegue.
            if anular_no_resolubles:
                _db.run_query("UPDATE ai_picks SET result='ANULADO', updated_at=NOW() WHERE id=%s", (p["id"],))
                anulados += 1
            else:
                _descartar(p)
            continue

        r = resolver_mercado(market_type, d, sel, linea)
        if r["result"] in ("ACIERTO", "FALLO"):
            _db.run_query("UPDATE ai_picks SET result=%s, updated_at=NOW() WHERE id=%s",
                          (r["result"], p["id"]))
            resueltos += 1
            detalles.append({
                "id": p["id"],
                "partido": f"{p.get('home_name')} {d.get('home_score')}-{d.get('away_score')} {p.get('away_name')}",
                "mercado": p.get("market"),
                "seleccion": p.get("selection"),
                "resultado": r["result"],
                "valor_real": r.get("value_real"),
            })
        else:
            if anular_no_resolubles:
                _db.run_query("UPDATE ai_picks SET result='ANULADO', updated_at=NOW() WHERE id=%s", (p["id"],))
                anulados += 1
            else:
                _descartar(p)

    return {
        "total_pendientes": len(picks),
        "resueltos": resueltos,
        "anulados": anulados,
        "sin_datos": sin_datos,
        "en_juego": en_juego,
        # Los mercados prohibidos (primer juego/punto) no se intentan: se
        # informan aparte para que el panel cuadre.
        "omitidos": omitidos,
        # Picks de mas de 72h que no se pudieron verificar y se borraron.
        "purgados": purgados,
        "detalle": detalles[:40],
    }

def search_prop(q: PropQuery):
    """Verifica un pick: ACIERTO / FALLO / NO_RESOLUBLE."""
    mercado_txt = ((q.market or "") + " " + (getattr(q, 'titulo', '') or "")).lower()
    if "primer juego" in mercado_txt or "primer punto" in mercado_txt:
        return {"result": "PENDIENTE", "source": None, "value_real": None,
                "confidence": 0, "message": "Mercado descartado."}
    d = buscar_completo(q.sport, q.home_name, q.away_name, q.event_date, q.league)
    if not d.get("found"):
        return {"result": "PENDIENTE", "source": None, "value_real": None,
                "confidence": 0, "message": "No se encontro el partido."}

    if q.player_name:
        _inyectar_jugador(d, q.player_name)

    r = resolver_mercado(q.market_type, d, q.selection, q.line_value)
    return {
        "result": r["result"],
        "source": (d.get("fuentes_usadas") or [None])[0],
        "value_real": r.get("value_real"),
        "confidence": r.get("confidence", 0),
        "partido": f"{d.get('home_name')} {d.get('home_score')}-{d.get('away_score')} {d.get('away_name')}",
    }
