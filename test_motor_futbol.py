# -*- coding: utf-8 -*-
"""Tests del motor Elo + Monte Carlo aplicado a FUTBOL.

Lo que se fija aqui:
  * El simulador de goles es Poisson de verdad (media correcta, empate posible,
    nada de goles negativos) y es reproducible con semilla.
  * El clasificador de mercados entiende 1X2, doble oportunidad, ambos marcan,
    totales y handicap, y RECHAZA lo que no sabe modelar (lineas de cuartos,
    mercados por tiempo).
  * Los ratings de futbol se guardan por LIGA (`soccer:eng.1`) y heredan la
    configuracion de `soccer`.
  * La medicion de 1X2 es la multiclase: medir futbol con el Brier binario de
    "gana el local" da 0.25 (moneda) y hace creer que el modelo no sirve.
"""
import unittest
from unittest import mock

from backend.apuestas import calibrar, elo, motor, simulacion


class TestSimuladorFutbol(unittest.TestCase):
    def test_media_de_goles_coincide_con_lambda(self):
        sim = simulacion.simular_futbol(1.6, 1.1, rho_equipos=0.0, n=8000,
                                        semilla=7)
        goles = [g for lote in sim["lotes"] for g in lote]
        media_h = sum(g[0] for g in goles) / len(goles)
        media_a = sum(g[1] for g in goles) / len(goles)
        self.assertAlmostEqual(media_h, 1.6, delta=0.12)
        self.assertAlmostEqual(media_a, 1.1, delta=0.12)

    def test_no_hay_goles_negativos_ni_fraccionarios(self):
        sim = simulacion.simular_futbol(0.4, 0.3, n=2000, semilla=3)
        for lote in sim["lotes"]:
            for gh, ga in lote:
                self.assertGreaterEqual(gh, 0)
                self.assertGreaterEqual(ga, 0)
                self.assertEqual(gh, int(gh))
                self.assertEqual(ga, int(ga))

    def test_el_empate_tiene_probabilidad_de_verdad(self):
        """Con una normal el empate sale 0%: por eso el futbol usa Poisson."""
        sim = simulacion.simular_futbol(1.4, 1.3, rho_equipos=0.0, n=6000,
                                        semilla=11)
        total = empates = 0
        for lote in sim["lotes"]:
            for gh, ga in lote:
                total += 1
                empates += gh == ga
        self.assertGreater(empates / total, 0.15)
        self.assertLess(empates / total, 0.40)

    def test_misma_semilla_mismo_resultado(self):
        a = simulacion.simular_futbol(1.5, 1.2, n=1000, semilla=42)
        b = simulacion.simular_futbol(1.5, 1.2, n=1000, semilla=42)
        self.assertEqual(a["lotes"], b["lotes"])

    def test_contrato_igual_que_nba(self):
        sim = simulacion.simular("soccer:eng.1", 1.4, 1.1, n=800)
        for clave in ("lotes", "n", "mu_local", "mu_visita", "p_ot"):
            self.assertIn(clave, sim)
        self.assertEqual(sim["p_ot"], 0.0)

    def test_el_precio_de_1x2_suma_uno(self):
        sim = simulacion.simular_futbol(1.5, 1.2, n=6000, semilla=5)
        probs = []
        for lado in ("local", "empate", "visita"):
            probs.append(simulacion.precio(
                sim, {"tipo": "resultado", "lado": lado})["prob"])
        self.assertAlmostEqual(sum(probs), 1.0, delta=0.005)


class TestCumpleMercadosFutbol(unittest.TestCase):
    def test_resultado(self):
        self.assertTrue(simulacion._cumple(2, 1, {"tipo": "resultado", "lado": "local"}))
        self.assertFalse(simulacion._cumple(2, 1, {"tipo": "resultado", "lado": "empate"}))
        self.assertTrue(simulacion._cumple(1, 1, {"tipo": "resultado", "lado": "empate"}))
        self.assertTrue(simulacion._cumple(0, 1, {"tipo": "resultado", "lado": "visita"}))

    def test_doble_oportunidad(self):
        self.assertTrue(simulacion._cumple(1, 1, {"tipo": "doble_oportunidad",
                                                  "lado": "local_o_empate"}))
        self.assertTrue(simulacion._cumple(0, 2, {"tipo": "doble_oportunidad",
                                                  "lado": "visita_o_empate"}))
        self.assertFalse(simulacion._cumple(1, 1, {"tipo": "doble_oportunidad",
                                                   "lado": "local_o_visita"}))
        self.assertTrue(simulacion._cumple(2, 0, {"tipo": "doble_oportunidad",
                                                  "lado": "local_o_visita"}))

    def test_ambos_marcan(self):
        self.assertTrue(simulacion._cumple(1, 1, {"tipo": "ambos_marcan",
                                                  "direccion": "si"}))
        self.assertFalse(simulacion._cumple(2, 0, {"tipo": "ambos_marcan",
                                                   "direccion": "si"}))
        self.assertTrue(simulacion._cumple(2, 0, {"tipo": "ambos_marcan",
                                                  "direccion": "no"}))

    def test_total_y_handicap(self):
        self.assertTrue(simulacion._cumple(2, 1, {"tipo": "total",
                                                  "direccion": "over", "linea": 2.5}))
        self.assertFalse(simulacion._cumple(1, 1, {"tipo": "total",
                                                   "direccion": "over", "linea": 2.5}))
        # Madrid -0.5: le vale ganar por 1. Con -1.5 tiene que ganar por 2.
        self.assertTrue(simulacion._cumple(2, 1, {"tipo": "handicap",
                                                  "lado": "local", "linea": -0.5}))
        self.assertFalse(simulacion._cumple(2, 1, {"tipo": "handicap",
                                                   "lado": "local", "linea": -1.5}))


class TestClasificadorFutbol(unittest.TestCase):
    HOME, AWAY = "Real Madrid", "Barcelona"

    def _clas(self, market, selection, titulo=""):
        return motor.clasificar({"market": market, "titulo": titulo or market,
                                 "selection": selection},
                                self.HOME, self.AWAY, "soccer:esp.1")

    def test_1x2_local(self):
        c = self._clas("Ganador", "Real Madrid")
        self.assertEqual((c["tipo"], c["lado"]), ("resultado", "local"))

    def test_1x2_visitante(self):
        c = self._clas("Ganador", "Barcelona")
        self.assertEqual((c["tipo"], c["lado"]), ("resultado", "visita"))

    def test_1x2_empate(self):
        c = self._clas("Ganador", "Empate")
        self.assertEqual((c["tipo"], c["lado"]), ("resultado", "empate"))

    def test_doble_oportunidad_1x(self):
        c = self._clas("Doble oportunidad", "1X")
        self.assertEqual((c["tipo"], c["lado"]),
                         ("doble_oportunidad", "local_o_empate"))

    def test_doble_oportunidad_x2(self):
        c = self._clas("Doble oportunidad", "X2")
        self.assertEqual((c["tipo"], c["lado"]),
                         ("doble_oportunidad", "visita_o_empate"))

    def test_ambos_marcan_si(self):
        c = self._clas("Ambos equipos marcan", "Si")
        self.assertEqual((c["tipo"], c["direccion"]), ("ambos_marcan", "si"))

    def test_total_under(self):
        c = self._clas("Total de goles", "Menos de 2.5")
        self.assertEqual((c["tipo"], c["direccion"], c["linea"]),
                         ("total", "under", 2.5))

    def test_handicap_entero(self):
        c = self._clas("Handicap asiatico", "Real Madrid -1.5")
        self.assertEqual((c["tipo"], c["lado"], c["linea"]),
                         ("handicap", "local", -1.5))

    def test_handicap_de_cuartos_se_rechaza(self):
        """-0.25 es media victoria / media derrota: simularla como -0.5 mentiria."""
        c = self._clas("Handicap asiatico", "Real Madrid -0.25")
        self.assertEqual(c["tipo"], "no_soportado")
        self.assertIn("cuartos", c["motivo"])

    def test_mercado_de_primera_parte_no_soportado(self):
        c = self._clas("Ganador primera parte", "Real Madrid")
        self.assertEqual(c["tipo"], "no_soportado")

    def test_la_nba_no_usa_el_clasificador_de_futbol(self):
        c = motor.clasificar(
            {"market": "Ganador (incl. prorroga)", "titulo": "Ganador",
             "selection": "Boston Celtics"},
            "Boston Celtics", "New York Knicks", "nba")
        self.assertEqual((c["tipo"], c["lado"]), ("ganador", "local"))


class TestEloFutbol(unittest.TestCase):
    def test_ruta_por_liga(self):
        self.assertEqual(elo._ruta("soccer:eng.1"), "soccer/eng.1")
        self.assertEqual(elo._ruta("soccer:esp.1"), "soccer/esp.1")
        self.assertIsNone(elo._ruta("soccer:"))

    def test_cfg_hereda_de_soccer(self):
        self.assertEqual(elo._cfg("soccer:eng.1")["hfa"], elo.CONFIG["soccer"]["hfa"])
        self.assertEqual(elo._cfg("nba")["hfa"], elo.CONFIG["nba"]["hfa"])

    def test_es_futbol(self):
        self.assertTrue(elo.es_futbol("soccer"))
        self.assertTrue(elo.es_futbol("soccer:eng.1"))
        self.assertFalse(elo.es_futbol("nba"))
        self.assertFalse(elo.es_futbol("nfl"))

    def test_el_0_0_es_un_resultado_valido(self):
        """En NBA un 0-0 es un dato roto; en futbol es un partido."""
        self.assertEqual(elo.CONFIG["soccer"]["rango_total"][0], 0)

    def test_temporadas_de_futbol_no_repiten_la_actual(self):
        """ESPN etiqueta el futbol por año de INICIO: pedir [actual, 2026, 2025]
        descargaba la temporada en curso dos veces y perdia una entera."""
        lista = elo._temporadas(3, "soccer:eng.1")
        self.assertIsNone(lista[0])
        self.assertEqual(len(lista), len(set(lista)))


class TestMetrica1x2(unittest.TestCase):
    def test_las_tres_probabilidades_suman_uno(self):
        p = calibrar.prob_1x2(1.5, 1.1)
        self.assertAlmostEqual(p["local"] + p["empate"] + p["visita"], 1.0, places=6)

    def test_equipos_iguales_reparten_local_y_visita(self):
        p = calibrar.prob_1x2(1.3, 1.3)
        self.assertAlmostEqual(p["local"], p["visita"], places=6)
        self.assertGreater(p["empate"], 0.2)

    def test_el_favorito_gana_mas(self):
        fuerte = calibrar.prob_1x2(2.2, 0.7)
        debil = calibrar.prob_1x2(0.7, 2.2)
        self.assertGreater(fuerte["local"], 0.6)
        self.assertGreater(debil["visita"], 0.6)

    def test_brier_multiclase_penaliza_el_error(self):
        seguro = {"local": 0.9, "empate": 0.05, "visita": 0.05}
        self.assertLess(calibrar._brier_1x2(seguro, 1), 0.05)
        self.assertGreater(calibrar._brier_1x2(seguro, -1), 1.0)


class TestClaveMotor(unittest.TestCase):
    def test_futbol_por_liga(self):
        from ai import analista_picks as A
        self.assertEqual(A._clave_motor("soccer", "eng.1"), "soccer:eng.1")
        self.assertEqual(A._clave_motor("soccer", "soccer/esp.1"), "soccer:esp.1")
        # Sin liga se asume LaLiga, igual que el modelo de forma.
        self.assertEqual(A._clave_motor("soccer", ""), "soccer:esp.1")

    def test_nba(self):
        from ai import analista_picks as A
        self.assertEqual(A._clave_motor("nba", ""), "nba")

    def test_deporte_desconocido(self):
        from ai import analista_picks as A
        self.assertIsNone(A._clave_motor("tennis", "atp"))


class TestCalibracionEnCaliente(unittest.TestCase):
    """`hay_calibracion` evita que una liga sin preparar bloquee una peticion.

    Sin esta comprobacion, `estado()` descarga la liga entera de ESPN (~30
    peticiones, ~40 s) la primera vez que se pide un pick de esa liga: en un lote
    de 25 picks serian minutos.
    """

    def tearDown(self):
        elo._memoria.pop("soccer:zz.1", None)

    def test_sin_bd_no_hay_calibracion(self):
        with mock.patch.object(elo, "_db", return_value=None):
            self.assertFalse(elo.hay_calibracion("soccer:zz.1"))

    def test_sin_fila_devuelve_false(self):
        class Base:
            def run_query(self, sql, params=None, fetchone=False):
                return None

        with mock.patch.object(elo, "_db", return_value=Base()):
            self.assertFalse(elo.hay_calibracion("soccer:zz.1"))

    def test_con_fila_devuelve_true(self):
        class Base:
            def run_query(self, sql, params=None, fetchone=False):
                return {"id": 1}

        with mock.patch.object(elo, "_db", return_value=Base()):
            self.assertTrue(elo.hay_calibracion("soccer:zz.1"))

    def test_si_la_bd_revienta_no_propaga(self):
        class Base:
            def run_query(self, *a, **k):
                raise RuntimeError("bd caida")

        with mock.patch.object(elo, "_db", return_value=Base()):
            self.assertFalse(elo.hay_calibracion("soccer:zz.1"))

    def test_la_memoria_manda(self):
        elo._memoria["soccer:zz.1"] = {"calibracion": {"a": 1.0}, "ratings": {},
                                       "ts": 0.0}
        with mock.patch.object(elo, "_db", return_value=None):
            self.assertTrue(elo.hay_calibracion("soccer:zz.1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

