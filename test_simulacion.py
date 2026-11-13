# -*- coding: utf-8 -*-
"""Tests del motor Monte Carlo (backend/apuestas/simulacion.py).

Que se comprueba y por que importa
----------------------------------
  * Que la simetria se respeta: si los dos equipos son iguales, cada uno gana
    ~50% (+ ventaja de local = 0 en el test). Si esto falla, todas las
    probabilidades del motor estan sesgadas.
  * Que la prorroga SUBE los totales. El modelo Normal de ai/analista_nba la
    ignoraba mientras el bookmaker la incluye en la linea: eso empuja
    sistematicamente los picks hacia el Under.
  * Que el intervalo [prob_low, prob_high] contiene a la probabilidad puntual.
"""

import unittest

from backend.apuestas import simulacion


class TestSimetria(unittest.TestCase):
    def test_equipos_iguales_ganan_igual(self):
        sim = simulacion.simular_nba(110.0, 110.0, 13.0, 0.0, n=8000,
                                     p_ot=0.0, sd_mu=0.0, semilla=1)
        local = simulacion.precio(sim, {"tipo": "ganador", "lado": "local"})
        visita = simulacion.precio(sim, {"tipo": "ganador", "lado": "visita"})
        self.assertAlmostEqual(local["prob"] + visita["prob"], 1.0, places=3)
        self.assertAlmostEqual(local["prob"], 0.5, delta=0.03)

    def test_favorito_gana_mas(self):
        sim = simulacion.simular_nba(118.0, 104.0, 13.0, 0.0, n=8000,
                                     p_ot=0.0, sd_mu=0.0, semilla=2)
        local = simulacion.precio(sim, {"tipo": "ganador", "lado": "local"})
        self.assertGreater(local["prob"], 0.70)


class TestMercados(unittest.TestCase):
    def setUp(self):
        self.sim = simulacion.simular_nba(112.0, 108.0, 13.0, 0.2, n=8000,
                                          p_ot=0.0, sd_mu=0.0, semilla=3)

    def test_total_over_mas_under_es_uno(self):
        over = simulacion.precio(self.sim, {"tipo": "total", "linea": 220.5,
                                            "direccion": "over"})
        under = simulacion.precio(self.sim, {"tipo": "total", "linea": 220.5,
                                             "direccion": "under"})
        self.assertAlmostEqual(over["prob"] + under["prob"], 1.0, places=4)

    def test_linea_mas_alta_baja_el_over(self):
        baja = simulacion.precio(self.sim, {"tipo": "total", "linea": 210.5,
                                            "direccion": "over"})
        alta = simulacion.precio(self.sim, {"tipo": "total", "linea": 235.5,
                                            "direccion": "over"})
        self.assertGreater(baja["prob"], alta["prob"])

    def test_handicap_positivo_favorece_al_equipo(self):
        # +10 le valen perder por menos de 10: mas probable que ganar seco.
        con_ventaja = simulacion.precio(self.sim, {"tipo": "handicap", "lado": "visita",
                                                   "linea": 10.0})
        sin_ventaja = simulacion.precio(self.sim, {"tipo": "ganador", "lado": "visita"})
        self.assertGreater(con_ventaja["prob"], sin_ventaja["prob"])

    def test_total_equipo_usa_su_marcador(self):
        local = simulacion.precio(self.sim, {"tipo": "total_equipo", "lado": "local",
                                             "linea": 100.5, "direccion": "over"})
        visita = simulacion.precio(self.sim, {"tipo": "total_equipo", "lado": "visita",
                                              "linea": 100.5, "direccion": "over"})
        self.assertGreater(local["prob"], visita["prob"])

    def test_intervalo_contiene_la_probabilidad(self):
        p = simulacion.precio(self.sim, {"tipo": "total", "linea": 220.5,
                                         "direccion": "over"})
        self.assertLessEqual(p["prob_low"], p["prob"] + 1e-9)
        self.assertGreaterEqual(p["prob_high"], p["prob"] - 1e-9)


class TestProrroga(unittest.TestCase):
    def test_la_prorroga_sube_el_total(self):
        sin_ot = simulacion.simular_nba(112.0, 108.0, 13.0, 0.2, n=8000,
                                        p_ot=0.0, sd_mu=0.0, semilla=4)
        con_ot = simulacion.simular_nba(112.0, 108.0, 13.0, 0.2, n=8000,
                                        p_ot=0.25, sd_mu=0.0, semilla=4)
        clas = {"tipo": "total", "linea": 222.5, "direccion": "over"}
        self.assertGreater(simulacion.precio(con_ot, clas)["prob"],
                           simulacion.precio(sin_ot, clas)["prob"])


class TestValorEsperado(unittest.TestCase):
    def test_formula(self):
        self.assertAlmostEqual(simulacion.valor_esperado(0.60, 2.0), 0.20, places=6)
        self.assertAlmostEqual(simulacion.valor_esperado(0.50, 2.0), 0.0, places=6)

    def test_datos_invalidos(self):
        self.assertIsNone(simulacion.valor_esperado(None, 2.0))
        self.assertIsNone(simulacion.valor_esperado(0.5, 1.0))
        self.assertIsNone(simulacion.valor_esperado(1.5, 2.0))


class TestReproducible(unittest.TestCase):
    def test_misma_semilla_mismo_resultado(self):
        a = simulacion.simular_nba(110.0, 110.0, 13.0, 0.1, n=2000,
                                   sd_mu=0.0, semilla=99)
        b = simulacion.simular_nba(110.0, 110.0, 13.0, 0.1, n=2000,
                                   sd_mu=0.0, semilla=99)
        self.assertEqual(a["lotes"], b["lotes"])


if __name__ == "__main__":
    unittest.main()
