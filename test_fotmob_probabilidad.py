# -*- coding: utf-8 -*-
"""Regresion: medicion real de futbol via FotMob y filtro del 70%."""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fotmob as F
import probabilidad as P


class TestMojibake(unittest.TestCase):
    """La API de FotMob sirve los nombres con doble UTF-8."""

    def test_repara_doble_utf8(self):
        self.assertEqual(F._repara("Bayern M\u00c3\u00bcnchen"), "Bayern M\u00fcnchen")

    def test_no_rompe_texto_normal(self):
        self.assertEqual(F._repara("Real Madrid"), "Real Madrid")

    def test_clave_ignora_acentos(self):
        self.assertEqual(F._clave("Bayern M\u00c3\u00bcnchen"), F._clave("Bayern Munchen"))


class TestProbabilidad(unittest.TestCase):

    def test_over_y_under_se_extraen(self):
        """'Under' tambien debe decirse de que equipo es el mercado."""
        self.assertEqual(P._linea_de("Arsenal Over 1.5"), ("over", 1.5))
        self.assertEqual(P._linea_de("Arsenal Under 2.5"), ("under", 2.5))
        pick = {"titulo": "Arsenal Under 2.5 goles", "selection": "Arsenal Under 2.5"}
        # Que no devuelva None solo por no encontrar el equipo en los campos.
        self.assertIsNotNone(P.probabilidad_pick(pick, "soccer"))

    def test_sin_equipo_devuelve_none(self):
        self.assertIsNone(P.probabilidad_pick({"titulo": "Over 1.5"}, "soccer"))


if __name__ == "__main__":
    unittest.main()
