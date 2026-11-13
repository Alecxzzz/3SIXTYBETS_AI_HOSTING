# -*- coding: utf-8 -*-
"""Tests de la ficha del partido (venue, arbitros, TV) y de la jugada a jugada.

Por que existen: estos datos venian en el `summary` de ESPN y se tiraban. Si
alguien "limpia" el parser o ESPN cambia la forma, el detalle del partido se
queda sin sede, sin arbitros y sin jugadas, y nadie se entera hasta que un
usuario lo reporta. Estos tests fijan el contrato (incluidas las DOS formas en
que ESPN devuelve los records: `record` y `records`).
"""

import unittest

import sports


class TestGameInfo(unittest.TestCase):
    PAYLOAD = {
        "gameInfo": {
            "venue": {
                "fullName": "Mortgage Matchup Center",
                "shortName": "Mortgage Matchup Center",
                "indoor": True,
                "address": {"city": "Phoenix", "state": "AZ"},
                "images": [{"href": "https://a.espncdn.com/i/venues/nba/day/2029.jpg"}],
            },
            "attendance": 17071,
            "officials": [
                {"displayName": "Eric Dalen", "order": 2,
                 "position": {"displayName": "Referee"}},
                {"displayName": "Scott Foster", "order": 1,
                 "position": {"displayName": "Referee"}},
                {"displayName": "Matt Myers", "order": 3,
                 "position": {"displayName": "Referee"}},
            ],
        },
        "broadcasts": [
            {"media": {"name": "Suns Live"}, "type": {"slug": "streaming"},
             "market": {"type": "Home"}, "isNational": False, "lang": "en"},
            {"media": {"name": "Arizona's Family 3TV"}, "type": {"slug": "tv"},
             "market": {"type": "Home"}, "isNational": False, "lang": "en"},
        ],
    }

    def test_sede_y_foto(self):
        info = sports._parse_game_info(self.PAYLOAD)
        self.assertEqual(info["venue"], "Mortgage Matchup Center")
        self.assertEqual(info["city"], "Phoenix")
        self.assertEqual(info["state"], "AZ")
        self.assertEqual(info["attendance"], 17071)
        self.assertIn("espncdn.com", info["image"])

    def test_arbitros_ordenados(self):
        info = sports._parse_game_info(self.PAYLOAD)
        nombres = [a["name"] for a in info["officials"]]
        # El orden de ESPN manda: 1, 2, 3 (Scott Foster primero).
        self.assertEqual(nombres, ["Scott Foster", "Eric Dalen", "Matt Myers"])

    def test_donde_verlo(self):
        info = sports._parse_game_info(self.PAYLOAD)
        nombres = [b["name"] for b in info["broadcasts"]]
        self.assertEqual(nombres, ["Suns Live", "Arizona's Family 3TV"])

    def test_payload_vacio_no_revienta(self):
        info = sports._parse_game_info({})
        self.assertEqual(info["venue"], "")
        self.assertEqual(info["officials"], [])
        self.assertEqual(info["broadcasts"], [])
        self.assertEqual(info["image"], "")


class TestJugadas(unittest.TestCase):
    PAYLOAD = {
        "plays": [
            {"sequenceNumber": 1, "text": "Jump ball", "homeScore": 0,
             "awayScore": 0, "scoringPlay": False,
             "clock": {"displayValue": "12:00"},
             "period": {"number": 1, "displayValue": "1st Quarter"},
             "type": {"text": "Jump Ball"},
             "team": {"id": "21", "displayName": "Phoenix Suns"}},
            {"sequenceNumber": 2, "text": "Devin Booker makes 2-pt jumper",
             "homeScore": 2, "awayScore": 0, "scoringPlay": True,
             "clock": {"displayValue": "11:32"},
             "period": {"number": 1, "displayValue": "1st Quarter"},
             "type": {"text": "Made Shot"},
             "team": {"id": "21", "displayName": "Phoenix Suns"}},
        ]
    }

    def test_campos_de_cada_jugada(self):
        jugadas = sports._parse_jugadas(self.PAYLOAD)
        self.assertEqual(len(jugadas), 2)
        ultima = jugadas[-1]
        self.assertEqual(ultima["texto"], "Devin Booker makes 2-pt jumper")
        self.assertEqual(ultima["local"], 2)
        self.assertEqual(ultima["reloj"], "11:32")
        self.assertEqual(ultima["periodo"], 1)
        self.assertTrue(ultima["anota"])
        self.assertEqual(ultima["equipo"], "Phoenix Suns")

    def test_recorta_por_el_final(self):
        muchas = {"plays": [dict(self.PAYLOAD["plays"][0], sequenceNumber=i,
                                 text=f"jugada {i}") for i in range(10)]}
        jugadas = sports._parse_jugadas(muchas, maximo=3)
        self.assertEqual([j["texto"] for j in jugadas],
                         ["jugada 7", "jugada 8", "jugada 9"])

    def test_payload_vacio(self):
        self.assertEqual(sports._parse_jugadas({}), [])
        self.assertEqual(sports._parse_jugadas({"plays": [{}]}), [])


class TestRecords(unittest.TestCase):
    """ESPN usa `record` en el summary y `records` en el scoreboard."""

    SINGULAR = {"record": [{"type": "total", "summary": "4-7"},
                           {"type": "away", "summary": "1-3"}]}
    PLURAL = {"records": [{"type": "total", "summary": "9-3"},
                          {"type": "home", "summary": "5-1"}]}

    def test_record_singular(self):
        self.assertEqual(sports._records_de(self.SINGULAR),
                         {"total": "4-7", "away": "1-3"})

    def test_records_plural(self):
        self.assertEqual(sports._records_de(self.PLURAL),
                         {"total": "9-3", "home": "5-1"})

    def test_sin_records(self):
        self.assertEqual(sports._records_de({}), {})
        self.assertEqual(sports._records_de({"record": [None, {}, {"type": "x"}]}), {})


class TestWinProbabilidad(unittest.TestCase):
    """La serie que alimenta el grafico: contexto por punto y resumen."""

    PAYLOAD = {
        "plays": [
            {"id": "p1", "text": "Jump ball", "homeScore": 0, "awayScore": 0,
             "period": {"number": 1}, "clock": {"displayValue": "12:00"}},
            {"id": "p2", "text": "Triple", "homeScore": 9, "awayScore": 2,
             "period": {"number": 1}, "clock": {"displayValue": "9:41"}},
        ],
        "winprobability": [
            {"playId": "p1", "homeWinPercentage": 0.55, "tiePercentage": 0.0},
            {"playId": "p2", "homeWinPercentage": 0.78, "tiePercentage": 0.0},
        ],
    }

    def test_cada_punto_lleva_el_contexto_de_su_jugada(self):
        serie = sports._parse_winprobability(self.PAYLOAD)
        self.assertEqual(len(serie), 2)
        ultimo = serie[-1]
        self.assertEqual(ultimo["local"], 0.78)
        self.assertAlmostEqual(ultimo["visita"], 0.22, places=4)
        self.assertEqual(ultimo["periodo"], 1)
        self.assertEqual(ultimo["reloj"], "9:41")
        self.assertEqual(ultimo["marcador_local"], 9)
        self.assertEqual(ultimo["marcador_visita"], 2)

    def test_resumen_da_los_maximos_de_los_dos_equipos(self):
        serie = sports._parse_winprobability(self.PAYLOAD)
        r = sports._resumen_winprob(serie)
        self.assertAlmostEqual(r["max_local"], 0.78, places=4)
        self.assertAlmostEqual(r["min_local"], 0.55, places=4)
        # El maximo del visita NO es el del local: es 1 - minimo del local.
        self.assertAlmostEqual(r["max_visita"], 0.45, places=4)
        self.assertEqual(r["n"], 2)

    def test_sin_datos(self):
        self.assertEqual(sports._parse_winprobability({}), [])
        self.assertEqual(sports._resumen_winprob([]), {})


if __name__ == "__main__":
    unittest.main()
