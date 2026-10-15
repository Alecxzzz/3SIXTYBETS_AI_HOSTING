"""Regresion del bug: mercados de STATS no se resuelven con el marcador.

Bug real (oct 2026): 'Tarjetas totales del partido: Over 2.5' con solo 2
tarjetas reales salio ACIERTO porque la rama over/under del resolver
deterministico comparaba contra el TOTAL DE GOLES del partido (el titulo
contenia 'totales' y la enganchaba). Este test fija que tarjetas/corners/
faltas devuelven None (los resuelve FotMob) y que los goles siguen
resolviendose solos con el marcador.
"""
import unittest

import dashboard


def _pick(market, titulo, selection=""):
    return {
        "sport": "soccer",
        "market": market,
        "titulo": titulo,
        "selection": selection,
        "home_name": "Manchester City",
        "away_name": "Paris Saint-Germain",
        "eventDate": "2026-10-13T19:00:00Z",
    }


def _detail(hs, as_):
    return {
        "state": "post",
        "teams": [
            {"homeAway": "home", "name": "Manchester City", "score": str(hs)},
            {"homeAway": "away", "name": "Paris Saint-Germain", "score": str(as_)},
        ],
    }


class TestStatsNoSeResuelvenConGoles(unittest.TestCase):
    def test_tarjetas_over_2_5_con_4_goles_no_acierta(self):
        pick = _pick("Tarjetas totales del partido",
                     "Tarjetas totales del partido: Over 2.5", "Over 2.5")
        self.assertIsNone(dashboard._resolver_deterministico(pick, _detail(2, 2)))

    def test_corners_over_3_5_con_4_goles_no_acierta(self):
        pick = _pick("Corners", "Corners totales del partido: Over 3.5", "Over 3.5")
        self.assertIsNone(dashboard._resolver_deterministico(pick, _detail(3, 1)))

    def test_faltas_under_no_se_resuelve_con_goles(self):
        pick = _pick("Faltas totales", "Faltas totales del partido: Under 24.5",
                     "Under 24.5")
        self.assertIsNone(dashboard._resolver_deterministico(pick, _detail(5, 4)))

    def test_goles_over_sigue_resolviendose(self):
        pick = _pick("Totales", "Over 2.5 goles del partido", "Over 2.5")
        self.assertEqual(dashboard._resolver_deterministico(pick, _detail(2, 2)),
                         "ACIERTO")

    def test_goles_under_falla_con_4_goles(self):
        pick = _pick("Totales", "Under 2.5 goles del partido", "Under 2.5")
        self.assertEqual(dashboard._resolver_deterministico(pick, _detail(3, 1)),
                         "FALLO")


if __name__ == "__main__":
    unittest.main()
