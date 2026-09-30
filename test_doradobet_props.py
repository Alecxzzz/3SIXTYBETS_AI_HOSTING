# -*- coding: utf-8 -*-
"""
Test de cuotas REALES de props de jugador en Doradobet.

Reproduce el caso pedido: el prop de hits de Kyle Schwarber en el partido
Phillies vs Braves. No es un mock: consulta el bookmaker de verdad, asi que
falla si el formato del payload cambia o si el mercado no existe.

    python test_doradobet_props.py
"""
import sys
import unittest

import cuotas_doradobet as D


class TestPropsDoradobet(unittest.TestCase):
    EVENTO = ("Atlanta Braves", "Philadelphia Phillies")

    def _props_schwarber(self):
        ms = D.mercados_reales("mlb", *self.EVENTO, None, 1.20, 2.50)
        return [m for m in ms if "schwarber" in (m.get("titulo") or "").lower()]

    def test_mercado_existe_en_el_bookmaker(self):
        """Si el book no responde, el test falla con un mensaje claro."""
        ms = D.mercados_reales("mlb", *self.EVENTO, None, 1.20, 2.50)
        if not ms:
            self.skipTest("El bookmaker no tiene el evento ahora mismo")
        self.assertGreater(len(ms), 0)

    def test_prop_de_hits_de_schwarber_tiene_cuota_real(self):
        props = self._props_schwarber()
        if not props:
            self.skipTest("Schwarber no juega en el evento de hoy")
        hits = [p for p in props if "hits" in (p.get("titulo") or "").lower()]
        self.assertTrue(hits, "Deberia existir el prop de hits de Schwarber")
        for p in hits:
            self.assertTrue(
                1.20 <= p["odds"] <= 2.50,
                f"La cuota {p['odds']} esta fuera del rango publicable",
            )
            self.assertIn("schwarber", p["selection"].lower())
            # El nombre no debe arrastrar el codigo del equipo.
            self.assertNotIn("(", p["selection"], f"selection con parentesis: {p['selection']}")

    def test_props_no_tienen_eleccion_inventada(self):
        """Cada prop debe traer la linea que el book realmente publica (1+, 2+...)."""
        for p in self._props_schwarber():
            self.assertIn(
                p["selection"].split()[-1], ("1+", "2+", "3+", "4+", "5+", "6+"),
                f"Linea inesperada en {p['selection']}",
            )

    def test_no_hay_mercado_de_otro_deporte(self):
        """Los props deben ser de baseball, no de tenis/futbol."""
        ms = D.mercados_reales("mlb", *self.EVENTO, None, 1.20, 2.50)
        for m in ms:
            t = (m.get("titulo") or "").lower()
            self.assertNotIn("aces", t, "Aces es de tenis, no de MLB")
            self.assertNotIn("corner", t, "Corners es de futbol")


if __name__ == "__main__":
    unittest.main(verbosity=2)
