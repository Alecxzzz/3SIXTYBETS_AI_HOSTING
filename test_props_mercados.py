# -*- coding: utf-8 -*-
"""
Tests del parser de props de jugador (backend/props/mercados.py).

Los nombres de mercado y de seleccion NO son inventados: estan copiados del
payload real de Doradobet del 30/10/2026 (Villarreal vs Espanyol y
Washington Wizards vs Charlotte Hornets). Si el bookmaker cambia el formato,
estos tests fallan y avisan antes que el apartado muestre datos falsos.

    python -m unittest test_props_mercados -v
"""

import unittest

from backend.props import mercados as M


def _detalle(*mercados):
    """Construye un GetEventDetails minimo con la forma de Altenar.

    Cada mercado es (nombre, [(nombre_odd, precio, sv), ...]).
    """
    odds, cms = [], []
    for i, (nombre, filas) in enumerate(mercados):
        ids = []
        for j, (odd_name, precio, sv) in enumerate(filas):
            oid = f"{i}-{j}"
            odds.append({"id": oid, "name": odd_name, "price": precio, "sv": sv})
            ids.append(oid)
        cms.append({"name": nombre, "desktopOddIds": [ids]})
    return {"odds": odds, "childMarkets": cms}


class TestFutbol(unittest.TestCase):
    def test_goleador_usa_la_odd_de_cualquier_momento(self):
        """'Goleador' tiene 3 grupos: Primero, Ultimo y Cualq. Momen.
        Solo el ultimo responde a 'mete gol en el partido'."""
        det = _detalle((
            "Goleador - Gerard Moreno (VIL)",
            [("Primero", 4.25, "0.5|ws:player:1871|1"),
             ("Cualq. Momen", 1.9, "0.5|ws:player:1871|1")],
        ))
        filas = M.props_de_detalle("soccer", det)
        self.assertEqual(len(filas), 1, filas)
        f = filas[0]
        self.assertEqual(f["jugador"], "Gerard Moreno")
        self.assertEqual(f["equipo"], "VIL")
        self.assertEqual(f["prop"], "goles")
        self.assertEqual(f["linea"], 0.5)
        self.assertEqual(f["cuota"], 1.9)

    def test_escalera_de_remates_a_puerta(self):
        det = _detalle((
            "Remates a Puerta - Gerard Moreno (VIL)",
            [("Más de 0.5", 1.1334, "0.5"),
             ("Más de 1.5", 1.8182, "1.5"),
             ("Más de 2.5", 3.6, "2.5")],
        ))
        filas = M.props_de_detalle("soccer", det)
        self.assertEqual(len(filas), 3)
        self.assertTrue(all(f["prop"] == "remates_puerta" for f in filas))
        self.assertEqual([f["linea"] for f in filas], [0.5, 1.5, 2.5])
        self.assertTrue(all(f["lado"] == "over" for f in filas))

    def test_mercados_no_comparables_se_descartan(self):
        det = _detalle(
            ("Goleador o el sustituto anotará - Gerard Moreno (VIL)",
             [("Sí", 1.9, "0.5")]),
            ("Goleador en cualquier momento y marcador exacto - Gerard Moreno (VIL)",
             [("Sí", 6.0, "0.5")]),
            ("Multigoleadores Gerard Moreno (VIL)", [("Sí", 8.0, "0.5")]),
            ("Pases - Gerard Moreno (VIL)", [("Más de 30.5", 1.9, "30.5")]),
        )
        self.assertEqual(M.props_de_detalle("soccer", det), [])

    def test_elegir_principal_toma_la_linea_cercana_a_dos(self):
        det = _detalle((
            "Remates a Puerta - Gerard Moreno (VIL)",
            [("Más de 0.5", 1.1334, "0.5"),
             ("Más de 1.5", 1.8182, "1.5"),
             ("Más de 2.5", 3.6, "2.5")],
        ))
        principal = M.elegir_principal(M.props_de_detalle("soccer", det))
        self.assertEqual(len(principal), 1)
        self.assertEqual(principal[0]["linea"], 1.5)


class TestNba(unittest.TestCase):
    def test_jugador_en_la_seleccion(self):
        det = _detalle((
            "Puntos (incl. prórroga)",
            [("Anthony Davis (WAS) Más De 9.5", 1.0125, "9.5"),
             ("Anthony Davis (WAS) Más De 17.5", 1.3334, "17.5"),
             ("Anthony Davis (WAS) Más De 24.5", 2.7, "24.5")],
        ))
        filas = M.props_de_detalle("nba", det)
        self.assertEqual(len(filas), 3)
        f = filas[2]
        self.assertEqual(f["jugador"], "Anthony Davis")
        self.assertEqual(f["equipo"], "WAS")
        self.assertEqual(f["prop"], "puntos")
        self.assertEqual(f["linea"], 24.5)
        self.assertEqual(f["cuota"], 2.7)
        principal = M.elegir_principal(filas)
        self.assertEqual(len(principal), 1)
        # |1.3334-1.95| = 0.617  <  |2.7-1.95| = 0.75  <  |1.0125-1.95| = 0.938
        # => de esta escalera, la linea "justa" que elige es 17.5.
        self.assertEqual(principal[0]["linea"], 17.5)

    def test_multis_de_jugador_no_son_props(self):
        det = _detalle((
            "Multis de jugador (single) (incl. prórroga)",
            [("Brandon Miller (CHA) to have 3+ 3pt-fg-made and CHA to win", 2.2, None)],
        ))
        self.assertEqual(M.props_de_detalle("nba", det), [])


class TestNfl(unittest.TestCase):
    def test_yardas_de_pase(self):
        det = _detalle((
            "Yardas de pases Más / Menos de Jugador (incl. prórroga)",
            [("Aaron Rodgers (PIT) Más De 223.5", 1.8696, "223.5")],
        ))
        filas = M.props_de_detalle("nfl", det)
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual(f["jugador"], "Aaron Rodgers")
        self.assertEqual(f["equipo"], "PIT")
        self.assertEqual(f["prop"], "yardas_pase")
        self.assertEqual(f["linea"], 223.5)


class TestMlb(unittest.TestCase):
    def test_hits_por_jugador_con_doble_parentesis(self):
        det = _detalle((
            "Hits totales por jugador (Kyle Schwarber (PHI)) (incl. extra innings)",
            [("1+", 1.59, "1"), ("2+", 3.4, "2")],
        ))
        filas = M.props_de_detalle("mlb", det)
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]["jugador"], "Kyle Schwarber")
        self.assertEqual(filas[0]["equipo"], "PHI")
        self.assertEqual(filas[0]["prop"], "hits")
        self.assertEqual([f["linea"] for f in filas], [1.0, 2.0])


class TestUtilidades(unittest.TestCase):
    def test_linea_compuesta(self):
        self.assertEqual(M._linea_de_sv("0.5|ws:player:1871|1"), 0.5)
        self.assertEqual(M._linea_de_sv("223.5"), 223.5)
        self.assertIsNone(M._linea_de_sv(None))

    def test_primero_y_ultimo_no_son_props(self):
        for nombre in ("Primero", "Último", "Ultimo"):
            self.assertTrue(M._es_otro_mercado(nombre), nombre)
        self.assertFalse(M._es_otro_mercado("Cualq. Momen"))

    def test_deporte_no_soportado(self):
        self.assertEqual(M.props_de_detalle("tennis", {"childMarkets": []}), [])
        self.assertEqual(M.props_de_detalle("soccer", None), [])


class TestMercadosRechazados(unittest.TestCase):
    """Mercados reales de NBA que NO se pueden comparar con el total del
    partido. Clasificarlos con substring producia edges falsos de +50%."""

    def _nba(self, mercado, jugador="Bam Adebayo", equipo="MIA"):
        return M.props_de_detalle("nba", _detalle((
            mercado,
            [(f"{jugador} ({equipo}) Más De 9.5", 2.05, "9.5")],
        )))

    def test_intentos_de_tiros_de_2_no_son_puntos(self):
        self.assertEqual(
            self._nba("Intentos de Tiros de 2 Puntos Más de/Menos de"), [])

    def test_primer_cuarto_no_es_el_partido(self):
        self.assertEqual(self._nba("1er cuarto - Rebotes"), [])
        self.assertEqual(self._nba("1er cuarto - Puntos"), [])
        self.assertEqual(self._nba("1er cuarto - Asistencias"), [])

    def test_combinado_bloqueos_robos_no_es_robos(self):
        self.assertEqual(self._nba("Bloq.-Robos Más de/Menos de"), [])

    def test_h2h_no_es_prop(self):
        self.assertEqual(self._nba("Rebotes de los jugadores H2H"), [])

    def test_total_del_equipo_no_es_prop(self):
        self.assertEqual(self._nba("Total de Rebotes del equipo"), [])

    def test_el_mercado_normal_si_pasa(self):
        """Contraste: 'Rebotes Más de/Menos de' SI es un prop valido."""
        filas = self._nba("Rebotes Más de/Menos de")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["prop"], "rebotes")

    def test_pases_de_nfl_no_se_rompe(self):
        """La palabra 'pases' no debe tumbar 'Yardas de pases' (NFL)."""
        filas = M.props_de_detalle("nfl", _detalle((
            "Yardas de pases Más / Menos de Jugador (incl. prórroga)",
            [("Aaron Rodgers (PIT) Más De 223.5", 1.8696, "223.5")],
        )))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["prop"], "yardas_pase")



class TestMercadoPrincipal(unittest.TestCase):
    """Altenar publica variantes del mismo mercado con el mismo nombre pero
    otro sportMarketId y otra escala de linea. Solo vale el partido completo."""

    def test_variante_con_menos_jugadores_se_descarta(self):
        nombre = "Total de Yardas de recepción  Más / Menos de Jugador (incl. prórroga)"
        det = {
            "odds": [
                {"id": "m1", "name": "Ashton Jeanty (LV) Más De 25.5", "price": 1.91, "sv": "25.5"},
                {"id": "m2", "name": "Brock Bowers (LV) Más De 66.5", "price": 1.90, "sv": "66.5"},
                {"id": "m3", "name": "Tre Tucker (LV) Más De 44.5", "price": 1.90, "sv": "44.5"},
                {"id": "v1", "name": "Ashton Jeanty (LV) Más De 0.5", "price": 1.91, "sv": "0.5"},
            ],
            "childMarkets": [
                {"name": nombre, "sportMarketId": 71925, "desktopOddIds": [["m1"]]},
                {"name": nombre, "sportMarketId": 71925, "desktopOddIds": [["m2"]]},
                {"name": nombre, "sportMarketId": 71925, "desktopOddIds": [["m3"]]},
                {"name": nombre, "sportMarketId": 77060, "desktopOddIds": [["v1"]]},
            ],
        }
        filas = M.props_de_detalle("nfl", det)
        self.assertEqual(sorted(f["linea"] for f in filas), [25.5, 44.5, 66.5])
        self.assertTrue(all(f["prop"] == "yardas_recepcion" for f in filas))


class TestElegirPrincipal(unittest.TestCase):
    def _prop(self, lado, cuota, linea):
        return {"jugador": "X", "equipo": "VIL", "prop": "puntos", "prop_raw": "Puntos",
                "lado": lado, "linea": linea, "cuota": cuota}

    def test_empate_casi_igual_prefiere_over(self):
        """over 1.95 (dist 0.00) vs under 1.90 (0.05+0.05): gana el over."""
        principal = M.elegir_principal([
            self._prop("over", 1.95, 20.5),
            self._prop("under", 1.90, 20.5),
        ])
        self.assertEqual(len(principal), 1)
        self.assertEqual(principal[0]["lado"], "over")

    def test_si_el_under_esta_mucho_mas_cerca_se_elige_el_under(self):
        """over 3.0 (1.05) vs under 1.96 (0.01+0.05): gana el under."""
        principal = M.elegir_principal([
            self._prop("over", 3.0, 20.5),
            self._prop("under", 1.96, 20.5),
        ])
        self.assertEqual(principal[0]["lado"], "under")



if __name__ == "__main__":
    unittest.main(verbosity=2)

