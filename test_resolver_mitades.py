# -*- coding: utf-8 -*-
"""
Test del resolver de mercados de MITAD (futbol).

Caso real: 'AtlA.tico Nacional gana cualquier mitad' en Nacional vs Junior
Barranquilla. El bookmaker lo publica a 1.27, pero el marcador FINAL no lo
determina: hacen falta los goles de cada tiempo, que salen de
competitor.linescores de ESPN.

Antes estos picks acababan ANULADOS (6 de 15 en produccion).
"""
import unittest

import dashboard as d

HOME = "Atl\u00e9tico Nacional"
AWAY = "Junior Barranquilla"


def detalle(home_mitades, away_mitades):
    """Detalle con marcador por periodos, como el que devuelve ESPN."""
    return {
        "state": "post",
        "teams": [
            {
                "name": HOME,
                "score": sum(home_mitades),
                "homeAway": "home",
                "linescores": [{"displayValue": str(x)} for x in home_mitades],
            },
            {
                "name": AWAY,
                "score": sum(away_mitades),
                "homeAway": "away",
                "linescores": [{"displayValue": str(x)} for x in away_mitades],
            },
        ],
    }


def pick(market=None, titulo="Nacional gana cualquier mitad: SI", sel="Si"):
    return {
        "market": market or f"{HOME} gana cualquier mitad",
        "titulo": titulo,
        "selection": sel,
    }


class TestGanaCualquierMitad(unittest.TestCase):
    """'Gana cualquier mitad' = ACIERTO si gana AL MENOS UNA."""

    def test_gana_la_primera(self):
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((1, 0), (0, 0))), "ACIERTO"
        )

    def test_gana_la_segunda(self):
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((0, 1), (0, 0))), "ACIERTO"
        )

    def test_gana_las_dos(self):
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((1, 1), (0, 0))), "ACIERTO"
        )

    def test_gana_una_perdiendo_el_resto(self):
        # Gana la 1a y pierde la 2a por goleada: sigue siendo ACIERTO.
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((1, 0), (0, 3))), "ACIERTO"
        )

    def test_no_gana_ninguna(self):
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((0, 0), (2, 0))), "FALLO"
        )

    def test_empate_ambos_tiempos_no_cuenta_como_ganar(self):
        # 0-0 y 0-0: no gana ninguna mitad.
        self.assertEqual(
            d._resolver_por_mitades(pick(), detalle((0, 0), (0, 0))), "FALLO"
        )

    def test_seleccion_no(self):
        p = pick(sel="No")
        self.assertEqual(d._resolver_por_mitades(p, detalle((1, 0), (0, 0))), "FALLO")

    def test_visitante_gana_una(self):
        """El pick puede ser del equipo visitante:Junior 1-0 y 0-1."""
        p = {"market": f"{AWAY} gana cualquier mitad",
             "titulo": "Barranquilla gana cualquier mitad: SI", "selection": "Si"}
        self.assertEqual(d._resolver_por_mitades(p, detalle((0, 0), (1, 0))), "ACIERTO")


class TestGanaAmbasMitades(unittest.TestCase):
    """'Gana ambas mitades' exige ganar LAS DOS: no se debe confundir."""

    def test_gana_las_dos(self):
        p = pick(market=f"{HOME} gana ambas mitades",
                 titulo="Nacional gana ambas mitades: SI")
        self.assertEqual(d._resolver_por_mitades(p, detalle((1, 1), (0, 0))), "ACIERTO")

    def test_gana_solo_una_es_fallo(self):
        p = pick(market=f"{HOME} gana ambas mitades",
                 titulo="Nacional gana ambas mitades: SI")
        self.assertEqual(d._resolver_por_mitades(p, detalle((1, 0), (0, 0))), "FALLO")

    def test_no_se_confunde_con_cualquier_mitad(self):
        """El bug original: 'cualquier' se resolvia como 'ambas'."""
        p = pick(market=f"{HOME} gana ambas mitades",
                 titulo="Nacional gana ambas mitades: SI")
        self.assertEqual(d._resolver_por_mitades(p, detalle((1, 0), (0, 0))), "FALLO")
        self.assertEqual(d._resolver_por_mitades(pick(), detalle((1, 0), (0, 0))), "ACIERTO")


class TestPrimeraMitad(unittest.TestCase):
    def test_1x2_primera_gana(self):
        p = pick(market="1a mitad - 1x2", titulo="Nacional 1a mitad - 1x2")
        self.assertEqual(d._resolver_por_mitades(p, detalle((2, 0), (0, 1))), "ACIERTO")

    def test_1x2_primera_pierde(self):
        p = pick(market="1a mitad - 1x2", titulo="Nacional 1a mitad - 1x2")
        self.assertEqual(d._resolver_por_mitades(p, detalle((0, 1), (2, 0))), "FALLO")


class TestNoInterfiere(unittest.TestCase):
    def test_mercado_normal_no_se_toca(self):
        p = {"market": "Ambos equipos marcan", "titulo": "Ambos marcan: SI", "selection": "Si"}
        self.assertIsNone(d._resolver_por_mitades(p, detalle((1, 1), (0, 0))))

    def test_sin_linescores_devuelve_none(self):
        det = {"state": "post", "teams": [
            {"name": HOME, "score": 1, "homeAway": "home", "linescores": []},
            {"name": AWAY, "score": 0, "homeAway": "away", "linescores": []},
        ]}
        self.assertIsNone(d._resolver_por_mitades(pick(), det))


if __name__ == "__main__":
    unittest.main(verbosity=2)
