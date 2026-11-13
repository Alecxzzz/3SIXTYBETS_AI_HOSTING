# -*- coding: utf-8 -*-
"""Tests del veredicto del analista contra el MERCADO.

Contexto real: el analista marcaba "VALOR / confianza ALTA" con EV del 39%,
porque solo miraba el EV y nunca comparaba con la cuota implicita. Un EV del
39% significa que el modelo discrepa 18 puntos de probabilidad de una casa de
apuestas liquida: eso es un error del modelo (lesiones, descanso, plantilla),
no una oportunidad.

Estos tests fijan la regla con los numeros que salieron en produccion.
"""

import unittest

from ai import analista_picks as A


class TestVeredictoMercado(unittest.TestCase):
    def test_discrepancia_grande_es_dudoso(self):
        # Spurs: 63.5% del modelo contra 45.5% de la cuota (2.20) -> +18 pts.
        veredicto, motivo = A._veredicto_mercado(0.6346, 2.20, 0.6346 * 2.20 - 1)
        self.assertEqual(veredicto, "DUDOSO")
        self.assertIn("discrepa", motivo)

    def test_discrepancia_negativa_es_evitar_no_dudoso(self):
        # Clippers: 47.8% contra 60.6% -> -12.8 pts. Como la cuota paga de
        # MENOS, el veredicto es EVITAR: el guardia solo frena falsos VALOR.
        veredicto, motivo = A._veredicto_mercado(0.4777, 1.65, 0.4777 * 1.65 - 1)
        self.assertEqual(veredicto, "EVITAR")
        self.assertIsNone(motivo)

    def test_valor_legitimo_se_respeta(self):
        # Magic: 60.2% contra 52.4% (1.91) -> +7.9 pts, dentro del tope.
        veredicto, motivo = A._veredicto_mercado(0.6023, 1.91, 0.6023 * 1.91 - 1)
        self.assertEqual(veredicto, "VALOR")
        self.assertIsNone(motivo)

    def test_neutro(self):
        veredicto, _ = A._veredicto_mercado(0.5636, 1.80, 0.5636 * 1.80 - 1)
        self.assertEqual(veredicto, "NEUTRO")

    def test_ev_absurdo_es_revisar(self):
        # Desvío pequeño pero EV enorme: imposible, se marca para revisar.
        with unittest.mock.patch.object(A, "MAX_DESVIO", 1.0):
            veredicto, motivo = A._veredicto_mercado(0.70, 2.00, 0.70 * 2.00 - 1)
        self.assertEqual(veredicto, "REVISAR")
        self.assertIn("demasiado grande", motivo)

    def test_sin_cuota(self):
        self.assertEqual(A._veredicto_mercado(0.6, 0, 0)[0], "SIN_CUOTA")
        self.assertEqual(A._veredicto_mercado(None, 1.9, 0)[0], "SIN_CUOTA")


class TestConfianza(unittest.TestCase):
    def test_dudoso_nunca_es_alta(self):
        self.assertEqual(A._confianza(10, 10, 0.39, "DUDOSO"), "BAJA")

    def test_revisar_nunca_es_alta(self):
        self.assertEqual(A._confianza(10, 10, 0.40, "REVISAR"), "BAJA")

    def test_valor_con_muestra_sigue_siendo_alta(self):
        self.assertEqual(A._confianza(10, 10, 0.09, "VALOR"), "ALTA")

    def test_muestra_corta_es_baja(self):
        self.assertEqual(A._confianza(1, 2, 0.10, "VALOR"), "BAJA")


if __name__ == "__main__":
    unittest.main()
