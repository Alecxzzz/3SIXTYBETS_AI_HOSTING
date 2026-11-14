# -*- coding: utf-8 -*-
"""Tests del emparejamiento de nombres bookmaker <-> ESPN.

Contexto real (2026-11-13, fecha FIFA): de 21 partidos de selecciones el book
solo emparejaba 1. ESPN escribe "England", "United States", "Curacao",
"Turkiye" y el book "Inglaterra", "Estados Unidos", "Curazao", "Turquia". Sin
traducir, ni las props ni las cuotas de las selecciones existian para el
sistema. Con el alias: 16 de 21.
"""
import unittest

import cuotas_doradobet as book


class TestAliasSelecciones(unittest.TestCase):
    def test_ingles_contra_espanol(self):
        for ingles, espanol in (("England", "Inglaterra"),
                                ("Spain", "España"),
                                ("United States", "Estados Unidos"),
                                ("Germany", "Alemania"),
                                ("Netherlands", "Países Bajos"),
                                ("Poland", "Polonia"),
                                ("Bosnia-Herzegovina", "Bosnia")):
            self.assertTrue(book._coincide_equipo(espanol, ingles),
                            f"{ingles} deberia emparejar con {espanol}")

    def test_acentos_y_cedillas_no_rompen(self):
        """`_norm` BORRABA la letra: 'Curaçao' -> 'curaao'. Ahora translitera."""
        for a, b in (("Curaçao", "Curazao"), ("Türkiye", "Turquía"),
                     ("México", "Mexico"), ("Perú", "Peru")):
            self.assertTrue(book._coincide_equipo(b, a), f"{a} vs {b}")

    def test_la_traduccion_va_en_los_dos_sentidos(self):
        self.assertTrue(book._coincide_equipo("England", "Inglaterra"))
        self.assertTrue(book._coincide_equipo("Inglaterra", "England"))

    def test_no_confunde_selecciones_distintas(self):
        self.assertFalse(book._coincide_equipo("España", "Portugal"))
        self.assertFalse(book._coincide_equipo("Brasil", "Argentina"))
        self.assertFalse(book._coincide_equipo("Inglaterra", "Irlanda"))
        self.assertFalse(book._coincide_equipo("France", "Germany"))
        # OJO: "Ireland" vs "Northern Ireland" SI empareja, y es a proposito: el
        # matcher acepta subcadena porque es lo que hace que "Real Madrid" case
        # con "Real Madrid CF". Lo que evita el falso positivo en produccion es
        # el filtro de fecha del evento (+-12 h), no el nombre.

    def test_los_clubes_siguen_igual(self):
        """La tabla es solo de selecciones: no debe tocar los clubes."""
        for club in ("Arsenal", "Real Madrid", "Boca Juniors", "América"):
            self.assertTrue(book._coincide_equipo(club, club), club)

    def test_no_rompe_el_matching_viejo_de_abreviaturas(self):
        """'ATL Braves' vs 'Atlanta Braves' ya funcionaba y tiene que seguir."""
        self.assertTrue(book._coincide_equipo("ATL Braves", "Atlanta Braves"))
        self.assertTrue(book._coincide_equipo("Real Madrid", "Real Madrid CF"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
