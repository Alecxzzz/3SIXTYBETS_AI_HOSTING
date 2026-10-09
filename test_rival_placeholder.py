# -*- coding: utf-8 -*-
"""
Regresion: NUNCA publicar un pick cuyo rival/oponente sea un placeholder.

Por que existe: en un partido de tenis ESPN devuelve 'TBD' cuando el oponente
aun no esta definido en el cuadro. Como 'TBD' no estaba en _NOMBRES_INVALIDOS,
_nombre_valido('TBD') daba True y el sistema publicaba un 'Handicap asiatico
Carlos Alcaraz -1.25' SIN saber contra quien juega: un pick imposible de
analizar y de resolver (acababa ANULADO). Estos tests fijan que esos
placeholders se rechazan tanto al GENERAR (_motivo_rechazo_pick) como al
MOSTRAR (_pick_calidad_ok), incluso para picks ya guardados en la BD.
"""
import io
import sys
import unittest

import dashboard as d

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


class TestRivalPlaceholder(unittest.TestCase):
    """Los placeholders de oponente no son nombres validos."""

    PLACEHOLDERS = ("TBD", "TBA", "To Be Determined", "To Be Announced",
                    "por definir", "a definir", "sin definir", "?", "N/A")

    def test_placeholders_no_son_nombre_valido(self):
        for nombre in self.PLACEHOLDERS:
            self.assertFalse(
                d._nombre_valido(nombre),
                f"'{nombre}' no deberia pasar como nombre de rival/equipo",
            )

    def test_nombres_reales_siguen_validos(self):
        for nombre in ("Carlos Alcaraz", "Novak Djokovic", "Real Madrid",
                       "LA Lakers", "New York Yankees"):
            self.assertTrue(
                d._nombre_valido(nombre),
                f"'{nombre}' es un nombre real y debe seguir siendo valido",
            )

    def test_pick_con_rival_tbd_no_se_muestra(self):
        pick = {
            "homeName": "Carlos Alcaraz",
            "awayName": "TBD",
            "odds": 1.38,
            "eventName": "TBD vs Carlos Alcaraz",
            "market": "handicap",
            "titulo": "Handicap asiatico Carlos Alcaraz -1.25",
            "selection": "Carlos Alcaraz -1.25",
            "rationale": "Gano 4/5 ultimos encuentros",
        }
        self.assertFalse(
            d._pick_calidad_ok(pick),
            "un pick con el rival en TBD no debe mostrarse en el dashboard",
        )

    def test_pick_valido_sigue_ok(self):
        pick = {
            "homeName": "Carlos Alcaraz",
            "awayName": "Novak Djokovic",
            "odds": 1.38,
            "eventName": "Novak Djokovic vs Carlos Alcaraz",
            "market": "handicap",
            "titulo": "Handicap asiatico Carlos Alcaraz -1.25",
            "selection": "Carlos Alcaraz -1.25",
            "rationale": "Gano 4/5 ultimos encuentros",
        }
        self.assertTrue(
            d._pick_calidad_ok(pick),
            "un pick con ambos nombres reales debe seguir siendo publicable",
        )

    def test_motivo_rechazo_calidad_cuando_rival_tbd(self):
        partido = {
            "sport": "tennis",
            "home_name": "Carlos Alcaraz",
            "away_name": "TBD",
            "event_name": "TBD vs Carlos Alcaraz",
            "league": "tennis/atp",
        }
        pick = {
            "market": "Ganador",
            "titulo": "Ganador del partido: Carlos Alcaraz",
            "selection": "Carlos Alcaraz",
            "odds": 1.38,
            "rationale": "Gano 4/5 ultimos encuentros",
        }
        # Con un mercado resoluble y disponible, el UNICO motivo de rechazo debe
        # ser 'calidad' (el rival 'TBD' no es un nombre valido), no otro filtro.
        motivo = d._motivo_rechazo_pick(
            pick, partido, ["Ganador", "Total de juegos"], "Ganador"
        )
        self.assertEqual(
            motivo, "calidad",
            "un rival TBD debe rechazarse por 'calidad' y no generar pick",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
