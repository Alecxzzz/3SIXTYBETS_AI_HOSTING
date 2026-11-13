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
        # Estos tests fijan el RESPALDO (modelo de forma): se fuerza Elo+MC a
        # "no disponible". El motor Elo+MC tiene sus propios tests en
        # test_motor.py / test_simulacion.py y en TestMotorElo.
        with mock.patch.object(A, "_salida_elo", return_value=None), \
                mock.patch.object(A, "forma_equipo", side_effect=[f_local, f_visita]):
            return A.analizar_pick(pick)

    def test_local_algo_mejor_da_valor_justificado(self):
        """Equipos parejos con ventaja de local a cuota 1.90: valor de verdad.

        La muestra es realista (no una paliza de 25 puntos): el modelo se queda
        a menos de 10 puntos de la cuota, que es cuando el VALOR se sostiene.
        """
        pick = {"id": "1", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador (incl. prorroga)",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": 1.90}
        r = self._analizar(pick, forma(112, 112), forma(111, 112))
        self.assertTrue(r["soportado"])
        self.assertGreater(r["prob_justa"], 0.5)
        self.assertEqual(r["deporte"], "nba")
        self.assertIn("Puntos esperados", A.informe_texto(r))
        # O sale VALOR, o el guardia lo frena por discrepar del mercado: nunca
        # un VEREDICTO sin motivo.
        self.assertIn(r["veredicto"], ("VALOR", "NEUTRO", "DUDOSO"))
        if r["veredicto"] == "DUDOSO":
            self.assertTrue(r.get("nota"))

    def test_modelo_muy_optimista_no_se_publica_como_valor(self):
        """Una diferencia de 25 puntos esperados NO es valor: es el modelo solo."""
        pick = {"id": "6", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador (incl. prorroga)",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": 1.90}
        r = self._analizar(pick, forma(122, 108), forma(103, 112))
        self.assertTrue(r["soportado"])
        self.assertEqual(r["veredicto"], "DUDOSO")
        self.assertEqual(r["confianza"], "BAJA")
        self.assertIn("discrepa", r["nota"])

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
        with mock.patch.object(A, "_salida_elo", return_value=None), \
                mock.patch.object(A, "forma_equipo", side_effect=[forma(120, 110), forma(110, 110)]), \
                mock.patch.object(A, "_promedios_equipo", return_value=[]):
            r = A.analizar_pick(pick)
        self.assertTrue(r["ok"])
        self.assertFalse(r["soportado"])
        self.assertIn("jugador", r["motivo"])


def _salida_elo_falsa(prob=0.58, low=0.55, high=0.61):
    """Lo que devolveria el motor Elo+MC para un pick NBA."""
    return {
        "ok": True, "deporte": "nba", "motor": "elo+mc",
        "partido": "Indiana Pacers vs Washington Wizards",
        "soportado": True, "mercado_detectado": "ganador",
        "distribucion": {"prob": prob},
        "prob_low": low, "prob_high": high,
        "goles_esperados": {"local": 114.2, "visita": 109.7},
        "fuerzas": {"n_local": 58, "n_visita": 57, "elo_local": 1615.0,
                    "elo_visita": 1417.0, "descanso_local": 2, "descanso_visita": 1},
        "info": "Elo 1615 vs 1417 · marcador esperado 114.2-109.7 · 10000 simulaciones",
    }


class TestMotorElo(unittest.TestCase):
    """El analista NBA debe usar Elo+MC de ahora en adelante."""

    def _pick(self, odds=1.90):
        return {"id": "9", "sport": "nba", "home_name": "Indiana Pacers",
                "away_name": "Washington Wizards", "market": "Ganador (incl. prorroga)",
                "titulo": "Ganador", "selection": "Indiana Pacers", "odds": odds}

    def test_usa_el_motor_elo_y_no_la_forma(self):
        with mock.patch.object(A, "_salida_elo", return_value=_salida_elo_falsa(0.58)), \
                mock.patch.object(A, "forma_equipo") as forma_mock:
            r = A.analizar_pick(self._pick())
        self.assertEqual(r["motor"], "elo+mc")
        self.assertAlmostEqual(r["prob_justa"], 0.58, places=4)
        # No se consulto la forma de ESPN: el motor manda.
        forma_mock.assert_not_called()
        self.assertEqual(r["veredicto"], "VALOR")
        self.assertIn("Elo 1615", r["info"])

    def test_si_el_mercado_no_se_modela_no_cae_al_modelo_viejo(self):
        salida = {"ok": True, "soportado": False, "deporte": "nba", "motor": "elo+mc",
                  "partido": "A vs B", "mercado_detectado": "no_soportado",
                  "motivo": "mercado no modelable"}
        with mock.patch.object(A, "_salida_elo", return_value=salida), \
                mock.patch.object(A, "forma_equipo") as forma_mock:
            r = A.analizar_pick(self._pick())
        self.assertFalse(r["soportado"])
        forma_mock.assert_not_called()

    def test_sin_cuota_el_motor_tampoco_inventa_valor(self):
        with mock.patch.object(A, "_salida_elo", return_value=_salida_elo_falsa(0.58)):
            r = A.analizar_pick(self._pick(odds=None))
        self.assertEqual(r["veredicto"], "SIN_CUOTA")

    def test_el_reporte_muestra_el_motor_y_el_intervalo(self):
        with mock.patch.object(A, "_salida_elo", return_value=_salida_elo_falsa(0.58)):
            r = A.analizar_pick(self._pick())
        txt = A.informe_texto(r)
        self.assertIn("Motor: elo+mc", txt)
        self.assertIn("IC", txt)
        self.assertIn("simulaciones", txt)

    def test_el_lote_cuenta_los_dudosos_y_el_motor(self):
        pick = self._pick(odds=2.20)  # 58% a 2.20: discrepancia de 12.5 pts
        with mock.patch.object(A, "_salida_elo", return_value=_salida_elo_falsa(0.58)):
            lote = A.analizar_lote([pick])
        self.assertEqual(lote["resumen"]["dudosos"], 1)
        self.assertEqual(lote["resumen"]["valor"], 0)
        self.assertEqual(lote["resumen"]["motor_elo_mc"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

