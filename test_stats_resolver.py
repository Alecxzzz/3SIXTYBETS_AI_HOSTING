# -*- coding: utf-8 -*-
"""Tests del resolutor de ESTADISTICAS (tarjetas / corners).

Aqui estaba el ultimo hueco: `_resolver_stats_sofascore` repetia el bug de
los unders (`startswith("m")`) y no tenia push.
"""
import unittest

from data_engine import resolver


class TestMetricas(unittest.TestCase):

    def test_tarjetas_totales_suma_amarillas_y_rojas(self):
        self.assertEqual(resolver.metricas_stats("Tarjetas totales Over 3.5"),
                         ("yellow_cards", "red_cards"))

    def test_amarillas_no_suma_rojas(self):
        self.assertEqual(resolver.metricas_stats("Tarjetas amarillas Over 2.5"),
                         ("yellow_cards",))

    def test_rojas_no_suma_amarillas(self):
        self.assertEqual(resolver.metricas_stats("Tarjetas rojas Over 0.5"),
                         ("red_cards",))

    def test_corners(self):
        self.assertEqual(resolver.metricas_stats("Total tiros de esquina Over 9.5"),
                         ("corners",))

    def test_mercado_que_no_es_de_estadistica(self):
        self.assertIsNone(resolver.metricas_stats("Over 2.5 goles"))


class TestDireccionStats(unittest.TestCase):

    def test_menos_de_es_under(self):
        # El bug: "menos de".startswith("m") daba over.
        self.assertEqual(resolver.direccion("Menos de 3.5 tarjetas"), "under")

    def test_over(self):
        self.assertEqual(resolver.direccion("Over 9.5 corners"), "over")


class TestTotalConPush(unittest.TestCase):

    def test_under_gana(self):
        self.assertEqual(resolver.resolver_total(2, 3.5, "under"), "ACIERTO")

    def test_under_pierde(self):
        self.assertEqual(resolver.resolver_total(4, 3.5, "under"), "FALLO")

    def test_over_gana(self):
        self.assertEqual(resolver.resolver_total(10, 9.5, "over"), "ACIERTO")

    def test_push_es_anulado(self):
        # 3 tarjetas con linea 3: devolucion, no FALLO.
        self.assertEqual(resolver.resolver_total(3, 3, "over"), "ANULADO")
        self.assertEqual(resolver.resolver_total(3, 3, "under"), "ANULADO")


class TestLado(unittest.TestCase):

    def test_nombre_especifico_gana(self):
        # 'Real Madrid' NO debe tragarse 'Real Madrid Castilla'.
        self.assertEqual(
            resolver.lado_en_texto("Corners de Real Madrid Castilla",
                                   "Real Madrid", "Real Madrid Castilla"),
            "away")


if __name__ == "__main__":
    unittest.main(verbosity=2)
