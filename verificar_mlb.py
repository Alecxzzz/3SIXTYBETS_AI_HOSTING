"""Verifica resultados reales de MLB para los picks cuestionados."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")
from backend.player_stats import mlb_api  # noqa: E402


def buscar_gamepk(team_name, fecha):
    tid = mlb_api.get_team_id(team_name)
    if not tid:
        print("no team id:", team_name)
        return None
    r = mlb_api.get_team_schedule(tid, 2026)
    for d in r.get("dates", []):
        for g in d.get("games", []):
            gd = d.get("date")
            if gd in (fecha,):
                return g.get("gamePk")
    return None


def mostrar(team_name, fecha):
    pk = buscar_gamepk(team_name, fecha)
    print(f"--- {team_name} {fecha} gamePk={pk}")
    if not pk:
        return
    box = mlb_api.get_boxscore(pk)
    for lado in ("away", "home"):
        eq = (box.get("teams") or {}).get(lado) or {}
        print(f"  [{lado}] {eq.get('team', {}).get('name')}")
        for pid, p in (eq.get("players") or {}).items():
            info = p.get("person") or {}
            full = (info.get("fullName") or "").lower()
            if any(x in full for x in ("mcneil", "sonny gray", "gray")):
                bat = (p.get("stats") or {}).get("batting") or {}
                pit = (p.get("stats") or {}).get("pitching") or {}
                print(
                    f"    {info.get('fullName')} | batting H={bat.get('hits')} AB={bat.get('atBats')} "
                    f"| pitching K={pit.get('strikeOuts')} IP={pit.get('inningsPitched')}"
                )


mostrar("Milwaukee Brewers", "2026-09-17")
mostrar("Boston Red Sox", "2026-09-17")
mostrar("New York Mets", "2026-09-17")
mostrar("Tampa Bay Rays", "2026-09-17")
mostrar("Athletics", "2026-09-17")
