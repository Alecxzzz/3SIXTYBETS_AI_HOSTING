# -*- coding: utf-8 -*-
"""Tests de la pagina /estadisticas aplicada a FUTBOL.

La pagina nacio para la NBA y decia "1er cuarto" en un partido de LaLiga 2. Aqui
se fija que:
  * El periodo se nombra segun el deporte (partes en futbol, cuartos en NBA).
  * La tabla de parciales usa columnas de partes, no 1-2-3-4.
  * Los goles y las tarjetas tienen su propio bloque (la jugada a jugada del
    futbol trae 200 acciones y los goles se pierden).
  * Si ESPN no publica alineaciones (habitual en segunda division) el bloque NO
    aparece: no se pinta una lista vacia que parece un error.
"""
import unittest

import estadisticas as E

PARTIDO = {
    "sport": "soccer",
    "event_id": "401",
    "league": "LaLiga 2 (Espana 2)",
    "status": "Final",
    "state": "post",
    "period": 2,
    "clock": "",
    "date": "2026-11-13T20:00Z",
    "teams": [],
    "home": {"name": "Burgos", "abbr": "BUR", "score": "2",
             "linescores": [1, 1], "logo": "",
             "records": [{"type": "total", "summary": "7-3"}]},
    "away": {"name": "Almeria", "abbr": "ALM", "score": "1",
             "linescores": [0, 1], "logo": "",
             "records": [{"type": "total", "summary": "8-2"}]},
    "game_info": {"venue": "El Plantio", "city": "Burgos"},
    "win_probability": [],
    "plays": [
        {"texto": "Gol de Burgos. Remate de cabeza", "reloj": "23'", "periodo": 1, "anota": True},
        {"texto": "Tarjeta amarilla para Almeria", "reloj": "41'", "periodo": 1, "anota": False},
        {"texto": "Gol de Almeria de penalti", "reloj": "58'", "periodo": 2, "anota": True},
        {"texto": "Cambio en Burgos", "reloj": "70'", "periodo": 2, "anota": False},
    ],
    "lineups": [
        {"equipo": "Burgos", "lado": "home", "formacion": "4-4-2",
         "titulares": [{"nombre": "Portero Uno", "numero": 1},
                       {"nombre": "Defensa Dos", "numero": 2}],
         "suplentes": [{"nombre": "Suplente Tres", "numero": 12}]},
        {"equipo": "Almeria", "lado": "away", "formacion": "4-2-3-1",
         "titulares": [{"nombre": "Portero Almeria", "numero": 13}],
         "suplentes": []},
    ],
}


class TestPaginaFutbol(unittest.TestCase):
    def test_el_periodo_se_nombra_como_parte_no_como_cuarto(self):
        self.assertEqual(E._cuarto(1, "soccer"), "1ª parte")
        self.assertEqual(E._cuarto(2, "soccer"), "2ª parte")
        self.assertEqual(E._cuarto(3, "soccer"), "Prórroga")
        self.assertEqual(E._cuarto(1, "nba"), "1er cuarto")
        self.assertEqual(E._cuarto(4, "nba"), "4to cuarto")

    def test_html_sin_cuartos_de_basket(self):
        html = E.pagina(PARTIDO)
        self.assertNotIn("1er cuarto", html)
        self.assertNotIn("4to cuarto", html)
        self.assertIn("1ª", html)

    def test_bloque_de_goles_y_tarjetas(self):
        html = E.pagina(PARTIDO)
        self.assertIn("Goles y tarjetas", html)
        self.assertIn("Gol de Burgos", html)
        self.assertIn("penalti", html)
        self.assertIn("Tarjeta amarilla", html)
        # Un cambio no es gol ni tarjeta: no debe colarse en ese bloque.
        bloque = html.split("Goles y tarjetas")[1].split("</div></div></div>")[0]
        self.assertNotIn("Cambio en Burgos", bloque)

    def test_alineaciones_cuando_existen(self):
        html = E.pagina(PARTIDO)
        self.assertIn("Alineaciones", html)
        self.assertIn("Portero Uno", html)
        self.assertIn("4-4-2", html)
        self.assertIn("Suplente Tres", html)

    def test_sin_alineaciones_no_se_pinta_el_bloque(self):
        partido = dict(PARTIDO)
        partido["lineups"] = [{"equipo": "Burgos", "titulares": [], "suplentes": []}]
        html = E.pagina(partido)
        self.assertNotIn("Alineaciones", html)

    def test_partido_sin_empezar_no_revienta(self):
        partido = dict(PARTIDO)
        partido.update({"state": "pre", "status": "11/14 - 20:00", "plays": [],
                        "lineups": [], "period": None})
        partido["home"] = dict(PARTIDO["home"], score=None, linescores=[])
        partido["away"] = dict(PARTIDO["away"], score=None, linescores=[])
        html = E.pagina(partido)
        self.assertIn("vs", html)
        self.assertNotIn("Goles y tarjetas", html)

    def test_la_nba_sigue_igual(self):
        partido = dict(PARTIDO)
        partido.update({"sport": "nba", "period": 3, "plays": [
            {"texto": "Triple de Tatum", "reloj": "7:12", "periodo": 3, "anota": True},
        ], "lineups": []})
        html = E.pagina(partido)
        self.assertIn("3er cuarto", html)
        # El bloque de goles es de futbol: en NBA no aparece.
        self.assertNotIn("Goles y tarjetas", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
