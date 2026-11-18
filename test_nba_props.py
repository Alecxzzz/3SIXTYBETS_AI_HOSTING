# -*- coding: utf-8 -*-
"""Tests de props NBA (backend/fuentes/nba/espn.py) — sin red."""
import unittest

from backend.fuentes.nba import espn

BOX = {
    "jayson tatum": {"nombre": "Jayson Tatum", "pts": 30.0, "reb": 8.0,
                     "ast": 5.0, "min": 36.0},
    "derrick white": {"nombre": "Derrick White", "pts": 12.0, "reb": 3.0,
                      "ast": 6.0, "min": 31.0},
    "bench guy": {"nombre": "Bench Guy", "pts": 0.0, "reb": 0.0,
                  "ast": 0.0, "min": None},
}


def prop(stat, direccion, linea, jugador="Jayson Tatum", market=None):
    sel = f"{jugador} {direccion} {linea} {stat}"
    return {"sport": "nba", "eventId": "x", "market": market or sel,
            "titulo": sel, "selection": sel}


class TestPropsNBA(unittest.TestCase):

    def test_over_gana(self):
        self.assertEqual(espn.resolver_prop(prop("puntos", "Mas de", 29.5), BOX), "ACIERTO")

    def test_over_pierde(self):
        self.assertEqual(espn.resolver_prop(prop("puntos", "Mas de", 30.5), BOX), "FALLO")

    def test_under_gana(self):
        self.assertEqual(espn.resolver_prop(prop("rebotes", "Menos de", 8.5), BOX), "ACIERTO")

    def test_push_es_anulado(self):
        self.assertEqual(espn.resolver_prop(prop("puntos", "Mas de", 30), BOX), "ANULADO")

    def test_jugador_que_no_jugo_se_anula(self):
        self.assertEqual(
            espn.resolver_prop(prop("puntos", "Mas de", 0.5, "Bench Guy"), BOX),
            "ANULADO")

    def test_la_estadistica_manda_la_seleccion(self):
        # El mercado dice "Puntos" pero la seleccion pide asistencias.
        p = prop("asistencias", "Mas de", 4.5, market="Puntos del jugador")
        self.assertEqual(espn.resolver_prop(p, BOX), "ACIERTO")

    def test_jugador_desconocido_no_decide(self):
        self.assertIsNone(espn.resolver_prop(prop("puntos", "Mas de", 20, "Otro Tipo"), BOX))

    def test_mercado_sin_estadistica(self):
        p = {"sport": "nba", "market": "Ganador", "titulo": "Celtics",
             "selection": "Celtics"}
        self.assertIsNone(espn.resolver_prop(p, BOX))


if __name__ == "__main__":
    unittest.main(verbosity=2)
