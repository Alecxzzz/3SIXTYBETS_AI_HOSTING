# -*- coding: utf-8 -*-
"""Tests del motor de decision (backend/apuestas/motor.py).

El caso central son las lineas REALES del feed de Doradobet, porque ahi es
donde un parser ingenuo se rompe:

    "selection": "Philadelphia 76ers +76"   -> NO es handicap de 76
    "selection": "Toronto Raptors (+16)"    -> SI es handicap de 16
    "selection": "Más de 223.5"             -> total over 223.5

`ai/analista_nba._linea_en` devuelve 76 en el primer caso (el "76" de "76ers").
El motor tiene que descartar ese mercado, no convertirlo en un pick.
"""

import unittest
from unittest import mock

from backend.apuestas import motor


class TestLineaDeEntrada(unittest.TestCase):
    def test_parentesis_gana_al_nombre(self):
        self.assertEqual(motor.linea_de_entrada("Toronto Raptors (+16)"), 16.0)
        self.assertEqual(motor.linea_de_entrada("Cavaliers (-5.5)"), -5.5)

    def test_mas_menos_de(self):
        self.assertEqual(motor.linea_de_entrada("Más de 223.5"), 223.5)
        self.assertEqual(motor.linea_de_entrada("Menos de 216"), 216.0)

    def test_signo_al_final(self):
        self.assertEqual(motor.linea_de_entrada("Lakers -3.5"), -3.5)

    def test_nombre_con_numero_no_confunde_si_hay_parentesis(self):
        # "76ers" contiene un 76, pero la linea real esta en el parentesis.
        self.assertEqual(motor.linea_de_entrada("Philadelphia 76ers (+4.5)"), 4.5)


class TestClasificar(unittest.TestCase):
    """Con datos copiados del feed real (11/12/2026)."""

    HOME = "Philadelphia 76ers"
    AWAY = "Toronto Raptors"

    def _clasificar(self, market, selection, titulo=""):
        return motor.clasificar({"market": market, "selection": selection,
                                 "titulo": titulo or f"{selection} ({market})"},
                                self.HOME, self.AWAY)

    def test_total_over(self):
        c = self._clasificar("Totales (incl. prórroga)", "Más de 223.5")
        self.assertEqual(c["tipo"], "total")
        self.assertEqual(c["direccion"], "over")
        self.assertAlmostEqual(c["linea"], 223.5)

    def test_handicap_con_parentesis(self):
        c = self._clasificar("Hándicap (incl. prórroga)", "Toronto Raptors (+16)")
        self.assertEqual(c["tipo"], "handicap")
        self.assertEqual(c["lado"], "visita")
        self.assertAlmostEqual(c["linea"], 16.0)

    def test_handicap_absurdo_se_descarta(self):
        # '76ers +76': la linea sale del nombre, no del mercado. Se descarta en
        # vez de publicar un handicap de 76 puntos.
        c = self._clasificar("Hándicap (incl. prórroga)", "Philadelphia 76ers +76")
        self.assertEqual(c["tipo"], "no_soportado")

    def test_mercado_por_cuartos_se_descarta(self):
        c = self._clasificar("Primer Cuarto  - Totales", "Más de 52.5")
        self.assertEqual(c["tipo"], "no_soportado")

    def test_prop_de_jugador_se_descarta(self):
        c = self._clasificar("Hacer un doble-doble (incl. prórroga)",
                             "Jaylen Brown (PHI) no")
        self.assertEqual(c["tipo"], "no_soportado")

    def test_ganador_identifica_el_lado(self):
        c = self._clasificar("Ganador (incl. prórroga)", self.AWAY)
        self.assertEqual(c["tipo"], "ganador")
        self.assertEqual(c["lado"], "visita")


class TestDecidir(unittest.TestCase):
    """Prueba las REGLAS de decision con la simulacion fijada.

    Se parchea `simulacion.precio` porque la simulacion real tiene un intervalo
    ancho (sd_mu=3 puntos) que depende del azar: para probar umbrales hace falta
    una probabilidad conocida, no una que cambie en cada corrida.
    """

    PARTIDO = {"event_id": "1", "home_name": "Philadelphia 76ers",
               "away_name": "Toronto Raptors", "home_id": "20",
               "away_id": "28", "date": "2026-11-13T00:00Z"}

    def _pre(self):
        return {"ok": True, "elo_local": 1600.0, "elo_visita": 1450.0,
                "elo_diff": 250.0, "descanso_local": 2.0, "descanso_visita": 1.0,
                "margen_esperado": 10.0, "total_esperado": 220.0,
                "mu_local": 115.0, "mu_visita": 105.0, "sigma_equipo": 13.0,
                "rho_equipos": 0.2, "p_ot": 0.065, "partidos_local": 60,
                "partidos_visita": 60}

    def _mercados(self, cuota_local):
        return [
            {"market": "Ganador (incl. prórroga)",
             "titulo": "Philadelphia 76ers (Ganador (incl. prórroga))",
             "selection": "Philadelphia 76ers", "odds": cuota_local, "linea": None},
        ]

    def _decidir(self, cuota, prob=0.80, prob_low=0.76, prob_high=0.85,
                 min_edge=0.03):
        precio = {"prob": prob, "prob_low": prob_low, "prob_high": prob_high,
                  "n": 20000}
        with mock.patch.object(motor, "predecir", return_value=self._pre()), \
                mock.patch.object(motor.simulacion, "precio", return_value=precio):
            return motor.decidir(self.PARTIDO, self._mercados(cuota), "nba",
                                 min_edge=min_edge, n_sims=1000, semilla=1)

    def test_con_valor_hay_pick(self):
        # p=0.80 con cuota 1.40: implicita 71.4% (8.6 puntos de desacuerdo, ok),
        # edge +12% y peor caso +6.4%. Es el caso que el motor DEBE apostar.
        pick = self._decidir(1.40)
        self.assertIsNotNone(pick)
        self.assertEqual(pick["selection"], "Philadelphia 76ers")
        self.assertGreater(pick["_edge"], 0.03)
        self.assertIn("Monte Carlo", pick["rationale"])
        self.assertIn("Modelo", pick["stats"][0])

    def test_sin_edge_no_hay_pick(self):
        # Misma probabilidad, cuota 1.20: la implicita (83.3%) es mayor que la
        # probabilidad del modelo. No hay valor.
        self.assertIsNone(self._decidir(1.20, prob=0.80, prob_low=0.76))

    def test_desacuerdo_grande_con_la_cuota_se_descarta(self):
        # p=0.80 contra una implicita de 52.6% son 27 puntos de desacuerdo en un
        # mercado liquido: el motor prefiere NO apostar antes que asumir que el
        # mercado esta equivocado.
        self.assertIsNone(self._decidir(1.90))

    def test_peor_caso_demasiado_malo_no_se_publica(self):
        # Edge medio positivo, pero el percentil 5 pierde 9% (mas del 5%
        # admitido): es una apuesta a que el modelo tiene razon, no a que el
        # mercado esta mal.
        self.assertIsNone(self._decidir(1.40, prob=0.80, prob_low=0.65))

    def test_sin_prediccion_devuelve_none(self):
        with mock.patch.object(motor, "predecir",
                               return_value={"ok": False, "motivo": "sin ratings"}):
            pick = motor.decidir(self.PARTIDO, self._mercados(1.45), "nba",
                                 n_sims=1000, semilla=1)
        self.assertIsNone(pick)

    def test_cuota_fuera_de_rango_se_ignora(self):
        # 9.00 esta fuera de ODDS_MINIMA/ODDS_MAXIMA: ni se evalua.
        self.assertIsNone(self._decidir(9.00))


class TestEvaluar(unittest.TestCase):
    def test_ordena_por_edge_y_deduplica(self):
        partido = {"event_id": "1", "home_name": "Philadelphia 76ers",
                   "away_name": "Toronto Raptors", "home_id": "20",
                   "away_id": "28"}
        pre = {"ok": True, "elo_local": 1600.0, "elo_visita": 1450.0,
               "elo_diff": 250.0, "descanso_local": None, "descanso_visita": None,
               "margen_esperado": 10.0, "total_esperado": 220.0,
               "mu_local": 115.0, "mu_visita": 105.0, "sigma_equipo": 13.0,
               "rho_equipos": 0.2, "p_ot": 0.065, "partidos_local": 60,
               "partidos_visita": 60}
        # Lineas pegadas al modelo (219.5 / 220.5): si se ponen lejos, la red de
        # seguridad por desvio en puntos las descarta, que es justo lo que
        # comprueba el test siguiente.
        mercados = [
            {"market": "Totales (incl. prórroga)", "selection": "Más de 219.5",
             "titulo": "Más de 219.5", "odds": 1.90},
            {"market": "Totales (incl. prórroga)", "selection": "Más de 219.5",
             "titulo": "Más de 219.5", "odds": 1.90},  # duplicado exacto
            {"market": "Totales (incl. prórroga)", "selection": "Menos de 220.5",
             "titulo": "Menos de 220.5", "odds": 1.90},
        ]
        with mock.patch.object(motor, "predecir", return_value=pre):
            ev = motor.evaluar(partido, mercados, "nba", n_sims=4000, semilla=5)
        self.assertTrue(ev["ok"])
        self.assertEqual(len(ev["candidatos"]), 2)
        self.assertGreaterEqual(ev["candidatos"][0]["edge"],
                                ev["candidatos"][-1]["edge"])

    def test_linea_muy_lejos_del_modelo_se_descarta(self):
        """El modelo pone 220 de total; la linea es 240.5. Ningun Elo sin
        noticias de lesiones sabe eso: se descarta en vez de publicar un 'edge'
        enorme que en realidad es sesgo del modelo."""
        partido = {"event_id": "2", "home_name": "Philadelphia 76ers",
                   "away_name": "Toronto Raptors", "home_id": "20",
                   "away_id": "28"}
        pre = {"ok": True, "elo_local": 1600.0, "elo_visita": 1450.0,
               "elo_diff": 250.0, "descanso_local": None, "descanso_visita": None,
               "margen_esperado": 10.0, "total_esperado": 220.0,
               "mu_local": 115.0, "mu_visita": 105.0, "sigma_equipo": 13.0,
               "rho_equipos": 0.2, "p_ot": 0.065, "partidos_local": 60,
               "partidos_visita": 60}
        mercados = [{"market": "Totales (incl. prórroga)",
                     "selection": "Menos de 240.5", "titulo": "Menos de 240.5",
                     "odds": 1.90}]
        with mock.patch.object(motor, "predecir", return_value=pre):
            ev = motor.evaluar(partido, mercados, "nba", n_sims=4000, semilla=5)
        self.assertEqual(len(ev["candidatos"]), 0)
        self.assertGreaterEqual(ev["descartados_discrepancia"], 1)


class TestDesvioEnPuntos(unittest.TestCase):
    PRE = {"margen_esperado": 10.0, "total_esperado": 220.0,
           "mu_local": 115.0, "mu_visita": 105.0}

    def test_total(self):
        d = motor._desvio_en_puntos(self.PRE, {"tipo": "total", "linea": 224.0})
        self.assertAlmostEqual(d, 4.0)

    def test_handicap_local(self):
        # El modelo espera +10 para el local; la linea es -8, o sea el mercado
        # espera +8: coinciden practicamente.
        d = motor._desvio_en_puntos(self.PRE, {"tipo": "handicap", "lado": "local",
                                              "linea": -8.0})
        self.assertAlmostEqual(d, 2.0)

    def test_handicap_visitante(self):
        d = motor._desvio_en_puntos(self.PRE, {"tipo": "handicap", "lado": "visita",
                                              "linea": 8.0})
        self.assertAlmostEqual(d, 2.0)

    def test_total_equipo(self):
        d = motor._desvio_en_puntos(self.PRE, {"tipo": "total_equipo",
                                              "lado": "local", "linea": 112.0})
        self.assertAlmostEqual(d, 3.0)

    def test_sin_linea(self):
        self.assertIsNone(motor._desvio_en_puntos(self.PRE, {"tipo": "ganador"}))


class TestReproducibilidad(unittest.TestCase):
    """La decision tiene que ser la MISMA con los mismos datos.

    Sin esto, dos corridas del generador podian publicar picks distintos (o uno
    si y otro no) sobre el mismo partido, y no habria forma de auditar nada.
    """

    PARTIDO = {"event_id": "401909978", "home_name": "Minnesota Timberwolves",
               "away_name": "Dallas Mavericks", "home_id": "16",
               "away_id": "6", "date": "2026-11-13T01:00Z"}
    PRE = {"ok": True, "elo_local": 1615.08, "elo_visita": 1417.11,
           "elo_diff": 298.0, "descanso_local": 4.0, "descanso_visita": 3.0,
           "margen_esperado": 11.2, "total_esperado": 225.7,
           "mu_local": 118.45, "mu_visita": 107.25, "sigma_equipo": 11.9,
           "rho_equipos": 0.26, "p_ot": 0.065, "partidos_local": 60,
           "partidos_visita": 60}

    MERCADOS = [
        {"market": "Ganador (incl. prórroga)",
         "titulo": "Minnesota Timberwolves (Ganador (incl. prórroga))",
         "selection": "Minnesota Timberwolves", "odds": 1.44, "linea": None},
        {"market": "Totales (incl. prórroga)", "selection": "Más de 225.5",
         "titulo": "Más de 225.5", "odds": 1.90, "linea": 225.5},
    ]

    def test_evaluar_dos_veces_da_lo_mismo(self):
        with mock.patch.object(motor, "predecir", return_value=self.PRE):
            a = motor.evaluar(self.PARTIDO, self.MERCADOS, "nba", n_sims=4000)
            b = motor.evaluar(self.PARTIDO, self.MERCADOS, "nba", n_sims=4000)
        self.assertEqual([c["prob"] for c in a["candidatos"]],
                         [c["prob"] for c in b["candidatos"]])

    def test_semilla_estable_entre_partidos_distintos(self):
        otro = dict(self.PARTIDO, event_id="401900000")
        self.assertNotEqual(motor._semilla(self.PARTIDO), motor._semilla(otro))
        self.assertEqual(motor._semilla(self.PARTIDO), motor._semilla(self.PARTIDO))


if __name__ == "__main__":
    unittest.main()

