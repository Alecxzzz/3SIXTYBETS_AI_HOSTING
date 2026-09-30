# -*- coding: utf-8 -*-
"""Regresion: el filtro de probabilidad real del 70%."""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probabilidad as P
import dashboard as d


class TestProbabilidad(unittest.TestCase):

    def test_umbral_70_configurado(self):
        self.assertEqual(d.PROBABILIDAD_MINIMA, 70)
        self.assertEqual(d.ULTIMOS_PARTIDOS, 10)

    def test_linea_de(self):
        self.assertEqual(P._linea_de("Over 1.5"), ("over", 1.5))
        self.assertEqual(P._linea_de("Under 3.5"), ("under", 3.5))
        self.assertEqual(P._linea_de("Schwarber 1+"), ("over_plus", 1))
        self.assertEqual(P._linea_de("Ganador"), (None, None))

    def test_linea_optima_ignora_over_cero(self):
        """Over 0 siempre acierta: no puede ser la linea optima."""
        linea, pct = P.linea_optima_por_promedio([1, 2, 0, 3, 1], "over")
        self.assertGreater(linea, 0)

    def test_linea_optima_usa_promedio(self):
        """'saco el promedio y busco la cantidad exacta'."""
        linea, pct = P.linea_optima_por_promedio([2, 3, 2, 3, 2], "over")
        self.assertEqual(linea, 2.0)
        self.assertEqual(pct, 100)

    def test_sin_datos_no_rompe(self):
        self.assertIsNone(P.probabilidad_pick({"titulo": "Ganador"}, "desconocido"))
        self.assertIsNone(P.probabilidad_pick({}, "mlb"))


if __name__ == "__main__":
    unittest.main()
