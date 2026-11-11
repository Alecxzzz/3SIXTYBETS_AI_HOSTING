# -*- coding: utf-8 -*-
"""
Tests del TENIS en el resolver (los picks que "nunca se resolvian").

Tres bugs reales, medidos con el panel admin (32 revisados, 0 resueltos):

1. ESPN no tiene /summary para tenis -> datos_completos devolvia found=False y
   el pick quedaba "sin datos" para siempre aunque el partido estuviera en el
   scoreboard. Ahora se arma desde el evento (_parse_evento_scoreboard).
2. ESPN deja score=null en tenis: el marcador son los SETS (linescores con su
   ganador). Sin calcularlo, no habia con que resolver.
3. ESPN marca homeAway al reves del pick (orden del cuadro): el veredicto salia
   INVERTIDO (Rune gano y se marcaba FALLO). Se alinea con los nombres del pick.
"""
import unittest

from data_engine.espn import _parse_evento_scoreboard
from data_engine.pick_parser import parsear_pick


def evento_tenis():
    """Holger Rune (home en ESPN) 2-0 Zizou Bergs, con sets 6-3 6-4."""
    return {
        "date": "2026-11-10T12:00Z",
        "name": "Bybit Stockholm Open",
        "status": {"type": {"state": "post"}},
        "competitions": [{
            "status": {"type": {"state": "post"}},
            "competitors": [
                {"homeAway": "away", "winner": False, "score": None,
                 "linescores": [{"value": 3.0, "winner": False}, {"value": 4.0, "winner": False}],
                 "athlete": {"displayName": "Zizou Bergs", "shortName": "Z. Bergs"}},
                {"homeAway": "home", "winner": True, "score": None,
                 "linescores": [{"value": 6.0, "winner": True}, {"value": 6.0, "winner": True}],
                 "athlete": {"displayName": "Holger Rune", "shortName": "H. Rune"}},
            ],
        }],
    }


class TestScoreboardTenis(unittest.TestCase):
    def test_calcula_sets_ganados(self):
        d = _parse_evento_scoreboard(evento_tenis(), "tennis", "Zizou Bergs", "Holger Rune")
        self.assertEqual(d["estado"], "post")
        self.assertEqual(d["home_name"], "Zizou Bergs")
        self.assertEqual(d["home_score"], 0.0)
        self.assertEqual(d["away_name"], "Holger Rune")
        self.assertEqual(d["away_score"], 2.0)

    def test_alinea_local_visitante_con_el_pick(self):
        """Rune es 'home' en ESPN pero 'away' en el pick: manda el pick."""
        invertido = _parse_evento_scoreboard(evento_tenis(), "tennis", "Holger Rune", "Zizou Bergs")
        self.assertEqual(invertido["home_name"], "Holger Rune")
        self.assertEqual(invertido["home_score"], 2.0)
        self.assertEqual(invertido["away_score"], 0.0)

    def test_partido_en_juego_no_inventa_marcador(self):
        ev = evento_tenis()
        ev["competitions"][0]["status"]["type"]["state"] = "in"
        d = _parse_evento_scoreboard(ev, "tennis", "Zizou Bergs", "Holger Rune")
        self.assertEqual(d["estado"], "in")


class TestParserTenis(unittest.TestCase):
    def _pick(self, market, selection, sport="tennis"):
        return {"id": "x", "sport": sport, "home_name": "Zizou Bergs",
                "away_name": "Holger Rune", "market": market, "titulo": market,
                "selection": selection}

    def test_handicap_de_set_es_resoluble(self):
        parsed = parsear_pick(self._pick("Hándicap de Set", "Holger Rune (+1.5)"))
        self.assertEqual(parsed, ("handicap", "away", 1.5))

    def test_handicap_de_juegos_no_se_resuelve_con_sets(self):
        """Sin marcador juego a juego, resolverlo con sets daria un falso."""
        self.assertIsNone(parsear_pick(self._pick("Hándicap de juego", "Holger Rune (+2.5)")))
        self.assertIsNone(parsear_pick(self._pick("Total juegos (under/over)", "Under 22.5")))

    def test_ganador_sigue_resoluble(self):
        self.assertEqual(parsear_pick(self._pick("Ganador", "Holger Rune")),
                         ("winner", "away", None))

    def test_primer_juego_sigue_prohibido(self):
        self.assertIsNone(parsear_pick(
            self._pick("Primer juego con servicio - Ganador", "Holger Rune")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
