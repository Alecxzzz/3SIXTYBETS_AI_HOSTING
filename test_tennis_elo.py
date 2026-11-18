# -*- coding: utf-8 -*-
"""Tests del motor ELO de tenis (backend/fuentes/tennis/*)."""
import unittest

from backend.fuentes.tennis import elo, sackmann


class TestScoreSackmann(unittest.TestCase):
    """El total de juegos se calcula del marcador; 7-6 son 13, no 25."""

    def test_dos_sets(self):
        self.assertEqual(sackmann.juegos_del_score("6-4 6-4"), 20)

    def test_tiebreak_no_suma_juegos_de_mas(self):
        # 7+6 = 13 (el tiebreak es UN juego, no 7 puntos).
        self.assertEqual(sackmann.juegos_del_score("7-6(5) 6-4"), 23)

    def test_tres_sets(self):
        self.assertEqual(sackmann.juegos_del_score("6-3 3-6 7-6(2)"), 31)

    def test_retirada_no_devuelve_total(self):
        self.assertIsNone(sackmann.juegos_del_score("6-3 2-1 RET"))
        self.assertIsNone(sackmann.juegos_del_score("W/O"))


class TestProbabilidades(unittest.TestCase):

    def test_ranking_mejor_gana_mas(self):
        self.assertGreater(elo.prob_ranking(1, 100), 0.5)
        self.assertLess(elo.prob_ranking(100, 1), 0.5)
        self.assertAlmostEqual(elo.prob_ranking(10, 10), 0.5)

    def test_ranking_invalido(self):
        self.assertIsNone(elo.prob_ranking(None, 10))
        self.assertIsNone(elo.prob_ranking(0, 10))

    def test_predice_con_ranking_y_sin_el(self):
        modelo = {"elo": {"a": 1700.0, "b": 1500.0},
                  "superficie": {}, "n_superficie": {}}
        con = elo.predecir(modelo, "a", "b", "Hard", rank_a=10, rank_b=50)
        self.assertGreater(con["prob"], 0.5)
        self.assertEqual(con["fuente"], "elo+ranking")
        sin = elo.predecir(modelo, "a", "b", "Hard")
        self.assertEqual(sin["fuente"], "elo")
        self.assertEqual(sin["prob"], sin["prob_elo"])

    def test_superficie_se_mezcla(self):
        modelo = {"elo": {"a": 1500.0}, "superficie": {("a", "Clay"): 1900.0},
                  "n_superficie": {("a", "Clay"): 50}}
        en_tierra = elo.predecir(modelo, "a", "b", "Clay")
        en_dura = elo.predecir(modelo, "a", "b", "Hard")
        self.assertGreater(en_tierra["prob"], en_dura["prob"])


class TestBacktest(unittest.TestCase):

    def test_el_favorito_claro_baja_el_brier(self):
        # 300 partidos identicos: 'fuerte' gana siempre al 'debil'.
        partidos = []
        for i in range(300):
            partidos.append({
                "winner_id": "fuerte", "loser_id": "debil",
                "surface": "Hard", "tourney_date": f"2024{(i % 12) + 1:02d}01",
                "rank_ganador": 1, "rank_perdedor": 100,
            })
        r = elo.backtest(partidos, "20240101")
        self.assertLess(r["modelos"]["blend"]["brier"], 0.25)
        self.assertAlmostEqual(r["moneda"]["brier"], 0.25)

    def test_formato_de_salida(self):
        r = elo.backtest([], "20240101")
        self.assertIn("modelos", r)
        self.assertIn("calibracion", r)
        self.assertEqual(r["modelos"]["elo"]["n"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
