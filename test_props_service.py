# -*- coding: utf-8 -*-
"""
Tests del calculo de racha/%/edge y del contrato con la BD.

No tocan la red ni MySQL: el detalle del bookmaker y el historico del jugador
se inyectan con datos fijos, y run_query se sustituye por un espia. Asi se
verifica la MATEMATICA (que es donde un error no se nota a simple vista).

    python -m unittest test_props_service -v
"""

import unittest
from unittest import mock

import db
from backend.props import service


def _historial(*valores_por_partido):
    return {
        "encontrado": True,
        "id": 999,
        "foto": "https://a.espncdn.com/i/headshots/soccer/players/full/999.png",
        "nombre": "Jugador Test",
        "partidos": [
            {"fecha": f"2026-10-0{i + 1}", "stats": stats}
            for i, stats in enumerate(valores_por_partido)
        ],
    }


class TestCalculo(unittest.TestCase):
    def test_over_cuenta_los_que_superan_la_linea(self):
        self.assertEqual(service._cumple([1, 0, 3, 2], 1.5, "over"), 2)

    def test_under_cuenta_los_que_se_quedan_por_debajo(self):
        self.assertEqual(service._cumple([0, 1, 0, 2], 0.5, "under"), 2)

    def test_linea_de_cero_cinco_cuenta_como_uno_o_mas(self):
        # "Goleador" es linea 0.5: basta 1 gol (0.5 no se cumple nunca).
        self.assertEqual(service._cumple([0, 1, 0], 0.5, "over"), 1)

    def test_fila_calcula_pct_implicita_y_edge(self):
        """1 de 3 partidos al 33.3% frente a una cuota de 2.0 (50%) = edge -16.7."""
        historial = _historial(
            {"totalShots": 1}, {"totalShots": 0}, {"totalShots": 3},
        )
        prop = {
            "jugador": "Jugador Test", "equipo": "VIL", "prop": "remates",
            "prop_raw": "Remates", "lado": "over", "linea": 1.5, "cuota": 2.0,
        }
        evento = {"id": "123", "name": "A vs B", "league": "LaLiga", "date": "2026-10-30T20:00Z"}
        fila = service._fila("soccer", prop, evento, historial, "totalShots", 5)
        self.assertEqual(fila["valores"], [1, 0, 3])
        self.assertEqual(fila["n_partidos"], 3)
        self.assertEqual(fila["cumple"], 1)
        self.assertEqual(fila["pct"], 33.3)
        self.assertEqual(fila["implicita"], 50.0)
        self.assertEqual(fila["edge"], -16.7)

    def test_fila_sin_datos_no_inventa(self):
        historial = {"encontrado": True, "partidos": [{"fecha": "2026-10-01", "stats": {}}]}
        prop = {
            "jugador": "X", "equipo": "VIL", "prop": "remates", "prop_raw": "Remates",
            "lado": "over", "linea": 1.5, "cuota": 2.0,
        }
        fila = service._fila("soccer", prop, {}, historial, "totalShots", 5)
        self.assertEqual(fila["valores"], [])
        self.assertEqual(fila["n_partidos"], 0)
        self.assertIsNone(fila["pct"])
        self.assertIsNone(fila["edge"])


class TestEvento(unittest.TestCase):
    """props_evento con bookmaker e historico inyectados (sin red)."""

    DETALLE = {
        "odds": [
            {"id": "a", "name": "Más de 1.5", "price": 1.8, "sv": "1.5"},
            {"id": "b", "name": "Más de 0.5", "price": 1.2, "sv": "0.5"},
            {"id": "c", "name": "Cualq. Momen", "price": 2.1, "sv": "0.5"},
            {"id": "d", "name": "Más de 0.5", "price": 1.9, "sv": "0.5"},
        ],
        "childMarkets": [
            {"name": "Remates a Puerta - Jugador Uno (VIL)", "desktopOddIds": [["a", "b"]]},
            {"name": "Goleador - Jugador Uno (VIL)", "desktopOddIds": [["c"]]},
            {"name": "Remates a Puerta - Jugador Dos (ESP)", "desktopOddIds": [["d"]]},
        ],
    }

    def test_cruza_bookmaker_con_historial(self):
        # 2/3 y 3/3 => >= 60%, pasan el filtro de calidad.
        historial_uno = _historial(
            {"shotsOnTarget": 2, "totalGoals": 1},
            {"shotsOnTarget": 3, "totalGoals": 1},
            {"shotsOnTarget": 1, "totalGoals": 0},
        )

        def _fake_historial(sport, jugador, equipo, evento, n):
            return historial_uno

        evento = {"id": "1", "name": "VIL vs ESP", "league": "LaLiga",
                  "date": "2026-10-30T20:00Z", "home": {"name": "VIL"},
                  "away": {"name": "ESP"}}
        with mock.patch.object(service.book, "detalle_de_evento", return_value=self.DETALLE), \
                mock.patch.object(service.proveedores, "historial_jugador", side_effect=_fake_historial):
            r = service.props_evento("soccer", evento, 5)

        self.assertTrue(r["ok"])
        props = r["props"]
        uno = [p for p in props if p["jugador"] == "Jugador Uno"]
        remates = [p for p in uno if p["prop"] == "remates_puerta"][0]
        self.assertEqual(remates["linea"], 1.5)
        self.assertEqual(remates["cumple"], 2)      # 2,3,1 -> 2 y 3
        self.assertEqual(remates["pct"], 66.7)
        self.assertEqual(remates["cuota_justa"], 1.5)
        self.assertIsNotNone(remates["pct_ajustado"])
        self.assertLess(remates["pct_ajustado"], remates["pct"])  # Wilson baja
        self.assertEqual(len(remates["detalle"]), 3)
        goles = [p for p in uno if p["prop"] == "goles"][0]
        self.assertEqual(goles["linea"], 0.5)

    def test_filtro_60_por_ciento_descarta_lo_demas(self):
        """1 de 3 (33%) no se publica: con 5 partidos es ruido."""
        historial = _historial(
            {"shotsOnTarget": 2}, {"shotsOnTarget": 0}, {"shotsOnTarget": 1},
        )
        evento = {"id": "1", "name": "VIL vs ESP", "league": "LaLiga",
                  "date": "2026-10-30T20:00Z", "home": {"name": "VIL"},
                  "away": {"name": "ESP"}}
        with mock.patch.object(service.book, "detalle_de_evento", return_value=self.DETALLE), \
                mock.patch.object(service.proveedores, "historial_jugador", return_value=historial):
            r = service.props_evento("soccer", evento, 5)

        self.assertTrue(r["ok"])
        # La invariante: NINGUNA prop publicada por debajo del minimo.
        self.assertTrue(all(p["pct"] >= service.PROPS_PCT_MINIMA for p in r["props"]))
        self.assertGreater(r["descartadas_por_pct"], 0)
        self.assertEqual(r["pct_minima"], service.PROPS_PCT_MINIMA)
        # La de 33% (remates a puerta linea 1.5 de Jugador Uno) NO sale.
        self.assertFalse([
            p for p in r["props"]
            if p["prop"] == "remates_puerta" and p["linea"] == 1.5
        ])

    def test_sin_detalle_del_book_no_hay_props(self):
        with mock.patch.object(service.book, "detalle_de_evento", return_value=None):
            r = service.props_evento("soccer", {"id": "1"}, 5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["props"], [])

    def test_jugador_sin_historial_no_aparece(self):
        sin = {"encontrado": False, "motivo": "no esta"}
        with mock.patch.object(service.book, "detalle_de_evento", return_value=self.DETALLE), \
                mock.patch.object(service.proveedores, "historial_jugador", return_value=sin):
            r = service.props_evento("soccer", {"id": "1", "home": {}, "away": {}}, 5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["props"], [])
        self.assertEqual(r["jugadores_sin_historico"], 2)


class TestPersistencia(unittest.TestCase):
    """El INSERT masivo debe tener tantos placeholders como parametros."""

    def _fila(self, **extra):
        base = {
            "sport": "soccer", "jugador": "Gerard Moreno", "equipo": "VIL",
            "foto": "http://x/y.png", "prop": "goles", "prop_raw": "Goleador",
            "lado": "over", "linea": 0.5, "cuota": 1.9, "implicita": 52.6,
            "valores": [0, 1, 0], "n_partidos": 3, "cumple": 1, "pct": 33.3,
            "edge": -19.3, "evento_id": "401", "evento": "VIL vs ESP",
            "liga": "LaLiga", "fecha_evento": "2026-10-30T20:00Z", "fuente": "espn",
        }
        base.update(extra)
        return base

    def test_insert_masivo_bien_formado(self):
        capturado = {}

        def _espia(sql, params=None, fetchone=False):
            capturado["sql"] = sql
            capturado["params"] = params
            return True

        filas = [self._fila(), self._fila(jugador="Otro Jugador")]
        with mock.patch.object(db, "run_query", side_effect=_espia):
            guardadas = db.upsert_player_props(filas)

        self.assertEqual(guardadas, 2)
        self.assertEqual(capturado["sql"].count("%s"), len(capturado["params"]))
        self.assertEqual(capturado["sql"].count("%s"), 2 * db._N_COLS_PLAYER_PROPS)
        self.assertIn("on duplicate key update", capturado["sql"])

    def test_filas_duplicadas_se_colapsan(self):
        capturado = {}

        def _espia(sql, params=None, fetchone=False):
            capturado["params"] = params
            return True

        with mock.patch.object(db, "run_query", side_effect=_espia):
            guardadas = db.upsert_player_props([self._fila(), self._fila()])

        self.assertEqual(guardadas, 1)
        self.assertEqual(len(capturado["params"]), db._N_COLS_PLAYER_PROPS)

    def test_reemplazar_sport_borra_lo_que_ya_no_viene(self):
        """El snapshot sustituye al del dia: se borran las props que sobran."""
        consultas = []

        def _espia(sql, params=None, fetchone=False):
            consultas.append((sql, params))
            return True

        with mock.patch.object(db, "run_query", side_effect=_espia):
            db.upsert_player_props([self._fila()], reemplazar_sport="soccer")

        deletes = [c for c in consultas if c[0].strip().lower().startswith("delete")]
        self.assertEqual(len(deletes), 1, consultas)
        sql, params = deletes[0]
        self.assertIn("pick_date = %s and sport = %s", sql)
        self.assertIn("id not in", sql)
        self.assertEqual(params[-1], db.player_prop_id(
            "401", "Gerard Moreno", "goles", "over", 0.5))

    def test_sin_filas_no_toca_la_bd(self):
        with mock.patch.object(db, "run_query") as espia:
            self.assertEqual(db.upsert_player_props([]), 0)
        espia.assert_not_called()

    def test_insert_por_lotes_de_150(self):
        """200 props -> 2 sentencias (150 + 50), no una gigante."""
        tamanos = []

        def _espia(sql, params=None, fetchone=False):
            tamanos.append(sql.count("%s"))
            return True

        filas = [self._fila(jugador=f"Jugador {i}") for i in range(200)]
        with mock.patch.object(db, "run_query", side_effect=_espia):
            guardadas = db.upsert_player_props(filas)

        self.assertEqual(guardadas, 200)
        self.assertEqual(len(tamanos), 2)
        self.assertEqual(tamanos[0], 150 * db._N_COLS_PLAYER_PROPS)
        self.assertEqual(tamanos[1], 50 * db._N_COLS_PLAYER_PROPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)



class TestScheduler(unittest.TestCase):
    """El ciclo diario: calendario -> props -> BD, sin red ni MySQL."""

    def test_refresh_guarda_las_props_de_cada_deporte(self):
        import props_scheduler

        props = [TestPersistencia()._fila()]
        resultado_deporte = {"ok": True, "props": props, "eventos": 3,
                             "eventos_con_props": 2}

        guardadas = []

        def _upsert(filas, fecha=None, reemplazar_sport=None):
            guardadas.append((len(filas), reemplazar_sport))
            return len(filas)

        with mock.patch.object(props_scheduler.service, "props_deporte",
                               return_value=resultado_deporte), \
                mock.patch.object(props_scheduler.db, "upsert_player_props",
                                  side_effect=_upsert), \
                mock.patch.object(props_scheduler.db, "purgar_player_props",
                                  return_value=0):
            estado = props_scheduler.refresh_once(usar_cache=False)

        self.assertEqual(len(guardadas), len(props_scheduler.DEPORTES))
        # Cada deporte se guarda reemplazando SU snapshot del dia.
        self.assertEqual([d for _n, d in guardadas], list(props_scheduler.DEPORTES))
        self.assertEqual(estado["props"], len(props_scheduler.DEPORTES))
        self.assertTrue(estado["ok"])
        self.assertIn("soccer", estado["detalle"])
        self.assertIn("2/3", estado["detalle"])

    def test_si_la_fuente_falla_no_rompe_el_ciclo(self):
        import props_scheduler

        with mock.patch.object(props_scheduler.service, "props_deporte",
                               side_effect=RuntimeError("book caido")), \
                mock.patch.object(props_scheduler.db, "upsert_player_props") as upsert, \
                mock.patch.object(props_scheduler.db, "purgar_player_props",
                                  return_value=0):
            estado = props_scheduler.refresh_once()

        upsert.assert_not_called()   # no se borra nada ni se guarda basura
        self.assertFalse(estado["ok"])
        self.assertIn("book caido", estado["detalle"])

    def test_intervalo_de_hora_y_media(self):
        import props_scheduler

        self.assertEqual(props_scheduler.INTERVAL_S, 90 * 60)
        self.assertEqual(props_scheduler.N_PARTIDOS, 5)

