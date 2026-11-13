# -*- coding: utf-8 -*-
"""Tests de los ratings Elo (backend/apuestas/elo.py).

Lo que se comprueba:
  * La aritmetica del ajuste (Gauss-Jordan y minimos cuadrados) recupera
    coeficientes conocidos. Sin esto, una matriz mal montada pasaria inadvertida
    porque el resultado sigue "pareciendo" un numero.
  * NO hay fuga de informacion: el Elo guardado en cada muestra es el de ANTES
    del partido. Si el modelo viera el resultado del partido que predice, el
    backtest saldria perfecto y no valdria nada.
  * El multiplicador por margen de victoria sube con el margen y baja con la
    diferencia de Elo.
"""

import unittest
from unittest import mock

from backend.apuestas import elo


class TestAlgebra(unittest.TestCase):
    def test_resolver_sistema_2x2(self):
        # 2x + y = 5 ; x + 3y = 10  ->  x=1, y=3
        sol = elo._resolver([[2, 1], [1, 3]], [5, 10])
        self.assertAlmostEqual(sol[0], 1.0, places=6)
        self.assertAlmostEqual(sol[1], 3.0, places=6)

    def test_resolver_singular_devuelve_none(self):
        self.assertIsNone(elo._resolver([[1, 1], [2, 2]], [1, 2]))

    def test_minimos_cuadrados_recupera_coeficientes(self):
        filas, y = [], []
        for x in range(-10, 11):
            filas.append([1.0, float(x)])
            y.append(2.0 + 3.0 * x)
        coef = elo._minimos_cuadrados(filas, y)
        self.assertAlmostEqual(coef[0], 2.0, places=6)
        self.assertAlmostEqual(coef[1], 3.0, places=6)

    def test_fecha_mysql(self):
        self.assertEqual(elo._fecha_mysql("2026-11-12T00:00Z"), "2026-11-12 00:00:00")
        self.assertEqual(elo._fecha_mysql("2026-11-12T03:30:00Z"), "2026-11-12 03:30:00")
        self.assertIsNone(elo._fecha_mysql(None))


class TestMov(unittest.TestCase):
    def test_paliza_mueve_mas_que_victoria_ajustada(self):
        self.assertGreater(elo._mov(25.0, 1500, 1500), elo._mov(2.0, 1500, 1500))

    def test_ganar_al_favorito_mueve_menos(self):
        sorpresa = elo._mov(10.0, 1400, 1600)
        esperado = elo._mov(10.0, 1600, 1400)
        self.assertGreater(sorpresa, esperado)


class TestConstruir(unittest.TestCase):
    """Construye ratings con partidos simulados, sin tocar la red."""

    def _partido(self, eid, fecha, h, a, sh, sa):
        return {"event_id": eid, "fecha": fecha,
                "home": {"id": h, "nombre": h, "score": float(sh)},
                "away": {"id": a, "nombre": a, "score": float(sa)},
                "prorroga": False}

    def _parchear(self, partidos):
        return [
            mock.patch.object(elo, "equipos",
                              return_value=[{"id": "A", "name": "A"},
                                            {"id": "B", "name": "B"}]),
            mock.patch.object(elo, "partidos_equipo",
                              side_effect=lambda sport, tid, season=None:
                              partidos.get(tid, [])),
        ]

    def test_ganador_sube_y_perdedor_baja(self):
        p1 = self._partido("1", "2026-01-01T00:00Z", "A", "B", 115, 95)
        parches = self._parchear({"A": [p1], "B": [p1]})
        with parches[0], parches[1]:
            ratings = elo.construir("nba", 1)["ratings"]
        self.assertGreater(ratings["A"]["rating"], elo.INICIAL)
        self.assertLess(ratings["B"]["rating"], elo.INICIAL)
        self.assertAlmostEqual(ratings["A"]["rating"] - elo.INICIAL,
                               elo.INICIAL - ratings["B"]["rating"], places=2)

    def test_sin_fuga_de_informacion(self):
        """El elo_h de la muestra tiene que ser el de ANTES de ese partido."""
        p1 = self._partido("1", "2026-01-01T00:00Z", "A", "B", 120, 90)
        p2 = self._partido("2", "2026-01-03T00:00Z", "A", "B", 120, 90)
        parches = self._parchear({"A": [p1, p2], "B": [p1, p2]})
        with parches[0], parches[1]:
            muestras = elo.construir("nba", 1)["muestras"]
        self.assertEqual(len(muestras), 2)
        self.assertEqual(muestras[0]["elo_h"], elo.INICIAL)
        self.assertEqual(muestras[0]["elo_a"], elo.INICIAL)
        self.assertGreater(muestras[1]["elo_h"], elo.INICIAL)

    def test_sin_datos_devuelve_vacio(self):
        with mock.patch.object(elo, "equipos", return_value=[]):
            datos = elo.construir("nba", 1)
        self.assertEqual(datos["ratings"], {})
        self.assertEqual(datos["muestras"], [])


class TestAjustar(unittest.TestCase):
    def _muestras(self, a_real, b_real, c_real=0.0, rest_varia=True, n=200):
        hfa = elo.CONFIG["nba"]["hfa"]
        salida = []
        for i in range(n):
            elo_h = 1500.0 + (i % 20) * 10.0
            elo_a = 1500.0 - (i % 17) * 10.0
            rest_h = 2.0 + (i % 4) if rest_varia else 3.0
            rest_a = 2.0 + ((i + 1) % 4) if rest_varia else 3.0
            d = (elo_h + hfa - elo_a) / 100.0
            salida.append({
                "elo_h": elo_h, "elo_a": elo_a,
                "descanso_h": rest_h, "descanso_a": rest_a,
                "total_h": 228.0, "total_a": 228.0,
                "margen": a_real + b_real * d + c_real * (rest_h - rest_a),
                "total": 228.0, "prorroga": False,
            })
        return salida

    def test_recupera_a_y_b(self):
        calib = elo.ajustar(self._muestras(-3.0, 4.5), {"total_medio": 228.0}, "nba")
        self.assertAlmostEqual(calib["a"], -3.0, places=2)
        self.assertAlmostEqual(calib["b"], 4.5, places=2)

    def test_recupera_el_efecto_del_descanso(self):
        calib = elo.ajustar(self._muestras(-3.0, 4.5, 0.8),
                            {"total_medio": 228.0}, "nba")
        self.assertAlmostEqual(calib["c"], 0.8, places=2)
        self.assertFalse(calib["sin_descanso"])

    def test_descanso_constante_no_rompe_el_ajuste(self):
        """Sin variacion en el descanso, el sistema es singular: el modelo tiene
        que reajustar sin esa columna, no devolver todo ceros."""
        calib = elo.ajustar(self._muestras(-3.0, 4.5, 0.0, rest_varia=False),
                            {"total_medio": 228.0}, "nba")
        self.assertTrue(calib["sin_descanso"])
        self.assertAlmostEqual(calib["b"], 4.5, places=2)
        self.assertEqual(calib["c"], 0.0)

    def test_prorroga_usa_referencia_si_no_se_puede_medir(self):
        """Si el endpoint no trae linescores, p_ot no puede quedarse en 0."""
        calib = elo.ajustar(self._muestras(0.0, 4.0),
                            {"total_medio": 228.0, "prorrogas_pct": 0.0}, "nba")
        self.assertAlmostEqual(calib["p_ot"], elo.P_OT_REFERENCIA["nba"], places=4)

    def test_sin_muestras_no_revienta(self):
        calib = elo.ajustar([], {"total_medio": 0.0}, "nba")
        self.assertEqual(calib["n_partidos"], 0)


class TestGuardarReemplaza(unittest.TestCase):
    """La calibracion se REEMPLAZA, no se acumula.

    Bug real: `guardar()` insertaba una fila nueva en cada calibracion y la
    tabla acabo con 7 filas de 'nba' (sigma_total 23.27 junto a 18.91). El motor
    leia la ultima, pero cualquier consulta sin ORDER BY veia otra calibracion.
    """

    def tearDown(self):
        # guardar() escribe en la cache de proceso: no debe filtrarse a otros tests.
        elo._memoria.pop("nba", None)

    def test_borra_la_calibracion_anterior_antes_de_insertar(self):
        llamadas = []

        class Base:
            def run_query(self, sql, params=None):
                llamadas.append(" ".join(str(sql).split()).lower())
                return [{"ok": 1}]

            def now_utc(self):
                return "2026-01-01 00:00:00"

        with mock.patch.object(elo, "_db", return_value=Base()):
            ok = elo.guardar(
                "nba", {"1": {"rating": 1500, "partidos": 10}},
                {"a": 1.0, "b": 2.0, "c": 0.0, "t0": -17.4, "t1": 1.05,
                 "sigma_margen": 14.5, "sigma_total": 18.9, "sigma_equipo": 11.9,
                 "rho": 0.1, "rho_equipos": 0.26, "p_ot": 0.065,
                 "total_medio": 229.2, "n_partidos": 2636})
        self.assertTrue(ok)
        self.assertIn("delete from elo_ratings where sport = %s", llamadas)
        self.assertIn("delete from elo_calibracion where sport = %s", llamadas)

        i_del = llamadas.index("delete from elo_calibracion where sport = %s")
        i_ins = next(i for i, c in enumerate(llamadas)
                     if c.startswith("insert into elo_calibracion"))
        # Si el insert fuera antes del delete, la clave unica (sport) lo rechaza.
        self.assertLess(i_del, i_ins)

    def test_si_la_tabla_de_ratings_no_responde_no_inserta_nada(self):
        llamadas = []

        class BaseRota:
            def run_query(self, sql, params=None):
                llamadas.append(" ".join(str(sql).split()).lower())
                return []

            def now_utc(self):
                return "2026-01-01 00:00:00"

        with mock.patch.object(elo, "_db", return_value=BaseRota()):
            ok = elo.guardar("nba", {"1": {"rating": 1500}}, {"a": 1.0})
        self.assertFalse(ok)
        self.assertFalse([c for c in llamadas if c.startswith("insert")])


if __name__ == "__main__":
    unittest.main()

