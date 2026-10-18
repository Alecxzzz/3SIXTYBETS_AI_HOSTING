"""
MOTOR DE BUSQUEDA DE DATOS COMPLETOS (ESPN).

Junta todo lo disponible de un partido (marcador, stats de equipo,
jugadores, eventos, odds) para que la IA se apoye 100% en datos reales.
"""
from typing import Optional, Dict, Any
from data_engine.espn import datos_completos_completo


def buscar_completo(sport: str, home: str, away: str,
                    fecha: Optional[str] = None, league: Optional[str] = None) -> Dict[str, Any]:
    """Datos COMPLETOS de un partido desde ESPN."""
    try:
        d = datos_completos_completo(sport, home, away, fecha, league)
        if d.get("found"):
            d["fuentes_usadas"] = ["espn"]
        return d
    except Exception as e:
        print(f"[buscador] error: {e}")
        return {"found": False, "raw": {}, "fuentes_usadas": []}


def contexto_para_ia(d: Dict[str, Any]) -> str:
    """Convierte los datos completos en TEXTO legible para la IA."""
    if not d or not d.get("found"):
        return "NO HAY DATOS DISPONIBLES de ese partido en las fuentes gratuitas."

    L = []
    L.append("=== DATOS REALES DEL PARTIDO (fuente: %s) ===" % ", ".join(d.get("fuentes_usadas", ["?"])))
    L.append("Partido: %s vs %s" % (d.get("home_name"), d.get("away_name")))
    L.append("Liga: %s | Estado: %s | Fecha: %s" % (d.get("league"), d.get("estado"), d.get("fecha")))
    L.append("MARCADOR: %s %s - %s %s" % (
        d.get("home_name"), d.get("home_score"), d.get("away_score"), d.get("away_name")))
    L.append("Ganador: %s" % d.get("winner"))

    L.append("")
    L.append("--- ESTADISTICAS DE EQUIPO ---")
    filas = [
        ("Corners", d.get("home_corners"), d.get("away_corners")),
        ("Faltas", d.get("home_fouls"), d.get("away_fouls")),
        ("Amarillas", d.get("home_yellow"), d.get("away_yellow")),
        ("Rojas", d.get("home_red"), d.get("away_red")),
        ("Tiros", d.get("home_shots"), d.get("away_shots")),
        ("Tiros a puerta", d.get("home_shots_on_target"), d.get("away_shots_on_target")),
        ("Atajadas", d.get("home_saves"), d.get("away_saves")),
        ("Posesion %", d.get("home_possession"), d.get("away_possession")),
    ]
    for nombre, h, a in filas:
        if h is not None or a is not None:
            L.append("  %s: %s - %s" % (nombre, h, a))
    L.append("  TOTALES: corners=%s | faltas=%s | tarjetas=%s" % (
        d.get("total_corners"), d.get("total_fouls"), d.get("total_cards")))

    if d.get("eventos_clave"):
        L.append("")
        L.append("--- EVENTOS CLAVE (goles, tarjetas) ---")
        for e in d["eventos_clave"][:12]:
            if e.get("jugador") or (e.get("tipo") or "").lower().find("goal") >= 0:
                L.append("  %s %s: %s (%s)" % (e.get("minuto"), e.get("tipo"), e.get("jugador"), e.get("equipo")))

    if d.get("leaders"):
        L.append("")
        L.append("--- JUGADORES DESTACADOS ---")
        for j in d["leaders"][:12]:
            L.append("  %s (%s) - %s: %s" % (j.get("nombre"), j.get("equipo"), j.get("categoria"), j.get("valor")))

    if d.get("jugadores"):
        L.append("")
        L.append("--- JUGADORES (%d registrados) ---" % len(d["jugadores"]))
        for j in d["jugadores"][:25]:
            st = ", ".join("%s=%s" % (k, v) for k, v in (j.get("stats") or {}).items() if v)
            L.append("  %s (%s) %s" % (j.get("nombre"), j.get("equipo"), st))

    if d.get("odds"):
        L.append("")
        L.append("--- CUOTAS ---")
        for o in d["odds"][:5]:
            L.append("  %s: %s (O/U %s)" % (o.get("proveedor"), o.get("detalle"), o.get("over_under")))

    L.append("")
    L.append("INSTRUCCION: usa SOLO estos datos reales. No inventes cifras.")
    return "\n".join(L)
