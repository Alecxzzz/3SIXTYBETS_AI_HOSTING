# -*- coding: utf-8 -*-
"""
Tests del MODELO NBA del analista (ai/analista_nba.py).

Por que existe: el panel mostraba "Analizados: 0 (solo fútbol)" aunque hubiera
picks NBA generados. Aquí se fija que la NBA también se analiza con datos de
ESPN (forma reciente por equipo + promedios de temporada por jugador) y que el
veredicto (VALOR/NEUTRO/EVITAR) sale de la probabilidad frente a la cuota.
"""
import unittest
from unittest import mock

from ai import analista_nba as N
from ai import analista_picks as A


def forma(pts, recibidos, n=6, local=True):
    """Forma sintética: n partidos con esos puntos a favor/en contra."""
    return [{"gf": float(pts), "ga": float(recibidos), "local": local,
             "rival": "Rival", "fecha": "2026-11-10"} for _ in range(n)]


class TestProbabilidades(unittest.TestCase):
    def test_over_y_under_suman_uno(self):
        for media, sd, linea in ((112, 12.5, 110.5), (115, 19, 220.5), (100, 13.5, 0)):
            self.assertAlmostEqual(
                N.prob_sobre(media, sd, linea) + N.prob_bajo(media, sd, linea), 1.0, places=6)

    def test_linea_igual_a_la_media_es_50(self):
        self.assertAlmostEqual(N.prob_sobre(110, 12.5, 110), 0.5, places=6)

    def test_sd_grande_aplana_la_probabilidad(self):
        cerca = N.prob_sobre(112, 5, 110)
        lejos = N.prob_sobre(112, 25, 110)
        self.assertGreater(cerca, lejos)


class TestPuntosEsperados(unittest.TestCase):
    def test_local_suma_ventaja_de_casa(self):
        esp = N.puntos_esperados(forma(115, 110), forma(115, 110))
        self.assertTrue(esp["ok"])
        self.assertGreater(esp["local"], esp["visita"])
        self.assertAlmostEqual(esp["local"] - esp["visita"], N.VENTAJA_LOCAL, places=1)

    def test_equipo_que_anota_mas_tiene_mas_puntos_esperados(self):
        esp = N.puntos_esperados(forma(125, 110), forma(105, 110))
        self.assertGreater(esp["local"], esp["visita"])
        self.assertGreater(esp["total"], 200)

    def test_sin_datos_devuelve_error_claro(self):
        esp = N.puntos_esperados([], [])
        self.assertFalse(esp["ok"])
        self.assertIn("puntos", esp["motivo"])


class TestClasificarMercado(unittest.TestCase):
    def _pick(self, market, selection, home="Indiana Pacers", away="Washington Wizards",
              titulo=None):
        return {"market": market, "titulo": titulo if titulo is not None else market,
                "selection": selection, "home_name": home, "away_name": away}

    def test_ganador(self):
        m = N.clasificar_mercado_nba(self._pick("Ganador (incl. prorroga)", "Indiana Pacers"))
        self.assertEqual((m["tipo"], m["lado"]), ("ganador", "local"))

    def test_handicap_con_linea(self):
        m = N.clasificar_mercado_nba(self._pick("Hándicap (incl. prórroga)",
                                                "Washington Wizards +4.5"))
        self.assertEqual((m["tipo"], m["lado"], m["linea"]), ("handicap", "visita", 4.5))

    def test_total_del_equipo(self):
        m = N.clasificar_mercado_nba(self._pick(
            "Oklahoma City Thunder Totales (incl. prórroga)", "Más de 111.5",
            home="Portland Trail Blazers", away="Oklahoma City Thunder"))
        self.assertEqual((m["tipo"], m["lado"], m["direccion"], m["linea"]),
                         ("total_equipo", "visita", "over", 111.5))

    def test_total_del_partido(self):
        m = N.clasificar_mercado_nba(self._pick("Total de puntos (incl. prorroga)",
                                                "Under 224.5"))
        self.assertEqual((m["tipo"], m["direccion"], m["linea"]), ("total", "under", 224.5))

    def test_prop_de_jugador(self):
        m = N.clasificar_mercado_nba(self._pick("Minimo de puntos del jugador",
                                                "Tyrese Haliburton Over 20.5"))
        self.assertEqual((m["tipo"], m["stat"], m["linea"]), ("prop", "pts", 20.5))

    def test_mercado_raro_no_soportado(self):
        m = N.clasificar_mercado_nba(self._pick("Primer cuarto ganador", "Pacers"))
        self.assertEqual(m["tipo"], "no_soportado")


class TestAnalisisCompleto(unittest.TestCase):
    """analizar_pick con NBA: veredicto real a partir de probabilidad y cuota."""

    def _analizar(self, pick, f_local, f_visita):
        with mock.patch.object(A, "forma_equipo", side_effect=[f_local, f_visita]):
            return A.analizar_pick(pick)

    def test_ganador_local_fuerte_da_valor(self):
        pick = {"id": "1", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador (incl. prorroga)",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": 1.90}
        r = self._analizar(pick, forma(122, 108), forma(103, 112))
        self.assertTrue(r["soportado"])
        self.assertIn(r["veredicto"], ("VALOR", "NEUTRO"))
        self.assertGreater(r["prob_justa"], 0.5)
        self.assertEqual(r["deporte"], "nba")
        self.assertIn("Puntos esperados", A.informe_texto(r))

    def test_under_imposible_es_evitar(self):
        pick = {"id": "2", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Total de puntos",
                "titulo": "Total de puntos", "selection": "Under 150.5", "odds": 1.90}
        r = self._analizar(pick, forma(120, 110), forma(118, 110))
        self.assertTrue(r["soportado"])
        self.assertEqual(r["veredicto"], "EVITAR")
        self.assertLess(r["prob_justa"], 0.2)

    def test_sin_cuota_no_inventa_valor(self):
        pick = {"id": "3", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": None}
        r = self._analizar(pick, forma(120, 110), forma(110, 110))
        self.assertEqual(r["veredicto"], "SIN_CUOTA")

    def test_sin_forma_de_espn_no_rompe(self):
        pick = {"id": "4", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": 1.9}
        r = self._analizar(pick, [], [])
        self.assertFalse(r["ok"])
        self.assertIn("ESPN", r["motivo"])

    def test_prop_sin_promedios_no_soportado(self):
        pick = {"id": "5", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Minimo de puntos del jugador",
                "titulo": "Minimo de puntos del jugador",
                "selection": "Jugador Inexistente Over 20.5", "odds": 1.85}
        with mock.patch.object(A, "forma_equipo", side_effect=[forma(120, 110), forma(110, 110)]), \
                mock.patch.object(A, "_promedios_equipo", return_value=[]):
            r = A.analizar_pick(pick)
        self.assertTrue(r["ok"])
        self.assertFalse(r["soportado"])
        self.assertIn("jugador", r["motivo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

