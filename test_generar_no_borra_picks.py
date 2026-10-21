# -*- coding: utf-8 -*-
"""
Test de regresion del generador: "Analizar y generar picks" NO debe perder
picks ya publicados.

Dos vias de destruccion que existian:

1. forzar=True borraba el pick del evento AL ENTRAR al partido, antes de que la
   IA generase el nuevo. Si la IA rechazaba (cuota fuera de rango, descarte
   reciente, liga floja, error...), el pick desaparecia y no volvia.
2. En cada ciclo se ejecutaba db.purgar_picks_pendientes_mercado("doble
   oportunidad"), que borraba TODOS los pendientes de ese mercado sin mirar
   fecha ni saturacion real: bastaba pulsar el boton dos veces para perder los
   picks recien generados (Champions incluida).
"""
import unittest
from unittest import mock

import dashboard


PARTIDO = {
    "sport": "soccer",
    "event_id": "champions-1",
    "event_name": "Feyenoord Rotterdam vs Real Betis",
    "home_name": "Feyenoord Rotterdam",
    "away_name": "Real Betis",
    "home_logo": None,
    "away_logo": None,
    "date": "2026-10-21T19:00Z",
    "league": "uefa.champions",
}


class GenerarNoBorraPicksTest(unittest.TestCase):
    def _generar(self, descartes):
        with mock.patch.object(dashboard, "_partidos_hoy", return_value=[dict(PARTIDO)]), \
                mock.patch.object(dashboard, "refrescar_ligas_flojas", return_value=None), \
                mock.patch.object(dashboard, "liga_floja", return_value=False), \
                mock.patch("db.descartes_recientes", return_value=descartes), \
                mock.patch("db.limpiar_descartes", return_value=True), \
                mock.patch("db.list_picks_hoy", return_value=[]), \
                mock.patch("db.eventos_con_pick", return_value={PARTIDO["event_id"]}), \
                mock.patch("db.delete_ai_picks_by_event_id") as borrar, \
                mock.patch("db.purgar_picks_pendientes_mercado") as purgar:
            res = dashboard.generar_picks_dia(max_partidos=5, forzar=True)
        return res, borrar, purgar

    def test_descarte_reciente_conserva_el_pick_anterior(self):
        """forzar=true + partido descartado: el pick publicado NO se borra."""
        res, borrar, purgar = self._generar({PARTIDO["event_id"]: "cuota baja"})

        borrar.assert_not_called()
        self.assertEqual(res["omitidos_descartados"], 1)
        self.assertEqual(res["generados"], 0)

    def test_no_se_purgan_pendientes_de_doble_oportunidad(self):
        """Generar no debe borrar picks por mercado en cada ciclo."""
        res, borrar, purgar = self._generar({PARTIDO["event_id"]: "cuota baja"})

        purgar.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
