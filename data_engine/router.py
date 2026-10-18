"""
Router FastAPI: motor de datos reales + verificacion de picks.
Se monta en main.py con app.include_router(data_engine.router).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import hashlib

from data_engine.buscador import buscar_completo, contexto_para_ia
from data_engine.rules import resolver_mercado

router = APIRouter(tags=["data-engine"])


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


@router.post("/search/prop")
def search_prop(q: PropQuery):
    """Verifica un pick: ACIERTO / FALLO / NO_RESOLUBLE."""
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
