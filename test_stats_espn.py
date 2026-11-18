# -*- coding: utf-8 -*-
"""Tests de las estadisticas de ESPN (faltas, corners, remates, tarjetas).

El summary de ESPN trae 28 estadisticas por equipo. Con ellas se resuelven
mercados que antes quedaban PENDIENTES para siempre, sin Sofascore (403) y sin
una peticion extra.

Cubre tambien el choque de idioma: el pick dice "Hungria" y ESPN "Hungary".

    python test_stats_espn.py
"""
import unittest

import dashboard


def detalle(sh, sa, estado="post"):
    return {
        "state": estado,
        "teams": [
            {"name": "Hungary", "score": 1, "homeAway": "home"},
            {"name": "Northern Ireland", "score": 1, "homeAway": "away"},
        ],
        "stats": {"home": sh, "away": sa},
    }


def pick(market, selection, titulo=None):
    return {"sport": "soccer", "market": market,
            "titulo": titulo or market, "selection": selection}


class TestStatsEspn(unittest.TestCase):

    def test_1x2_faltas_con_nombre_en_espanol(self):
        """El pick dice 'Hungria'; ESPN dice 'Hungary'. Debe encontrarlo."""
        d = detalle({"foulsCommitted": "12"}, {"foulsCommitted": "7"})
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("1x2 Faltas", "Hungría"), d), "ACIERTO")
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("1x2 Faltas", "Irlanda del Norte"), d), "FALLO")

    def test_corners_over_under(self):
        d = detalle({"wonCorners": "5"}, {"wonCorners": "4"})   # total 9
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("Tiros de esquina", "Over 8.5"), d), "ACIERTO")
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("Tiros de esquina", "Under 8.5"), d), "FALLO")

    def test_push_es_anulado(self):
        d = detalle({"wonCorners": "5"}, {"wonCorners": "4"})   # total 9
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("Tiros de esquina", "Over 9"), d), "ANULADO")

    def test_corners_de_un_equipo(self):
        d = detalle({"wonCorners": "5"}, {"wonCorners": "4"})
        self.assertEqual(
            dashboard._resolver_stats_espn(
                pick("Corners de Hungría", "Hungría Over 4.5"), d), "ACIERTO")

    def test_remates_a_puerta(self):
        d = detalle({"shotsOnTarget": "2"}, {"shotsOnTarget": "6"})
        self.assertEqual(
            dashboard._resolver_stats_espn(
                pick("Remates a puerta", "Irlanda del Norte Over 5.5"), d), "ACIERTO")

    def test_primera_mitad_no_se_toca(self):
        """ESPN no da stats por periodo: eso lo resuelve FotMob, no esto."""
        d = detalle({"wonCorners": "5"}, {"wonCorners": "4"})
        self.assertIsNone(
            dashboard._resolver_stats_espn(
                pick("1ª mitad - Tiros de esquina", "Over 4.5"), d))

    def test_sin_estadisticas_no_inventa(self):
        d = detalle({}, {})
        self.assertIsNone(
            dashboard._resolver_stats_espn(pick("1x2 Faltas", "Hungría"), d))

    def test_partido_no_terminado(self):
        d = detalle({"foulsCommitted": "12"}, {"foulsCommitted": "7"}, estado="in")
        # El resolver de arriba solo se llama con state=post, pero por si acaso:
        self.assertEqual(
            dashboard._resolver_stats_espn(pick("1x2 Faltas", "Hungría"), d), "ACIERTO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
