# -*- coding: utf-8 -*-
"""
Test de regresion: `data_engine/espn.datos_completos` SIN fecha.

Por que existe: en la rama "no me pasaron fecha" se importaba solo
`timedelta` y `timezone`, pero la linea siguiente usaba `datetime`:

    from datetime import timedelta, timezone as tz
    now_utc = datetime.utcnow()          # NameError

Cualquier llamada sin fecha moria con
"name 'datetime' is not defined" y el buscador devolvia found=False, que el
panel admin contaba como "sin datos" (picks que nunca se resolvian).
"""
import unittest
from unittest import mock

from data_engine import espn


class TestDatosCompletosSinFecha(unittest.TestCase):
    def test_no_revienta_por_falta_de_import(self):
        with mock.patch.object(espn, "buscar_partido", return_value=None) as buscar:
            res = espn.datos_completos("soccer", "Equipo A", "Equipo B")
        self.assertFalse(res.get("found"))
        # Si el NameError siguiera, esta linea no se alcanzaria.
        buscar.assert_called_once()
        fecha = buscar.call_args[0][3]
        self.assertIsNotNone(fecha, "deberia rellenar la fecha (ET)")
        # ESPN solo entiende YYYYMMDD: con "2026-11-09" el scoreboard da 0 eventos.
        self.assertRegex(fecha, r"^\d{8}$")

    def test_usa_la_fecha_recibida_sin_tocarla(self):
        with mock.patch.object(espn, "buscar_partido", return_value=None) as buscar:
            espn.datos_completos("soccer", "A", "B", fecha="2026-11-09")
        self.assertEqual(buscar.call_args[0][3], "2026-11-09")


if __name__ == "__main__":
    unittest.main(verbosity=2)
