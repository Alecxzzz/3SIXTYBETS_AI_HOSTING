# -*- coding: utf-8 -*-
"""
Test de verificacion de picks de MITAD/CUARTO en el data-engine
(endpoint POST /resolve/picks): pick_parser.parsear_pick + rules.resolver_mercado
con el marcador por periodos (linescores de ESPN).

Bug original: parsear_pick descartaba TODO mercado de mitad como 'sin datos'
(return None), asi que esos picks quedaban PENDIENTE para siempre y el panel
admin reportaba 'sin_datos' en masa (medido: 18 de 31 en una pasada).
"""
import unittest

from data_engine.pick_parser import parsear_pick
from data_engine.rules import resolver_mercado

HOME = "Atlético Nacional"
AWAY = "Junior Barranquilla"


def partido(home_periodos, away_periodos, **kw):
    d = {
        "found": True,
        "estado": "post",
        "home_name": HOME,
        "away_name": AWAY,
        "home_score": float(sum(home_periodos)),
        "away_score": float(sum(away_periodos)),
        "home_periodos": [float(x) for x in home_periodos],
        "away_periodos": [float(x) for x in away_periodos],
    }
    d.update(kw)
    return d


def pick(market, titulo, sel, home=HOME, away=AWAY):
    return {"market": market, "titulo": titulo, "selection": sel,
            "home_name": home, "away_name": away}


def resolver(p, d):
    parsed = parsear_pick(p)
    if not parsed:
        return "NO_PARSEABLE"
    mt, sel, lv = parsed
    return resolver_mercado(mt, d, sel, lv)["result"]


class TestParserMitades(unittest.TestCase):
    """El parser ya NO devuelve None para mercados de mitad."""

    def test_1a_mitad_1x2_si(self):
        self.assertEqual(
            parsear_pick(pick("1a mitad - 1x2", "Nacional 1a mitad - 1x2", "Si")),
            ("winner_periodo", "home|mitad|1", None))

    def test_1a_mitad_1x2_empate(self):
        self.assertEqual(
            parsear_pick(pick("1a mitad - 1x2", "1a mitad - 1x2", "Empate")),
            ("winner_periodo", "draw|mitad|1", None))

    def test_gana_cualquier_mitad_si(self):
        self.assertEqual(
            parsear_pick(pick(f"{HOME} gana cualquier mitad",
                              "Nacional gana cualquier mitad: SI", "Si")),
            ("cualquier_mitad_si", "home", None))

    def test_gana_cualquier_mitad_no(self):
        self.assertEqual(
            parsear_pick(pick(f"{HOME} gana cualquier mitad",
                              "Nacional gana cualquier mitad: NO", "No")),
            ("cualquier_mitad_no", "home", None))

    def test_gana_ambas_mitades(self):
        self.assertEqual(
            parsear_pick(pick(f"{HOME} gana ambas mitades",
                              "Nacional gana ambas mitades: SI", "Si")),
            ("ambas_mitades_si", "home", None))

    def test_total_goles_1a_mitad(self):
        self.assertEqual(
            parsear_pick(pick("Total de goles 1a mitad",
                              "Total de goles 1a mitad: Over 1.5", "Over 1.5")),
            ("over_under_periodo", "over|mitad|1", 1.5))

    def test_total_equipo_1a_mitad(self):
        p = pick("Primera mitad - Los Angeles Lakers total de puntos",
                 "Primera mitad - Los Angeles Lakers total de puntos: Over 55.5",
                 "Over 55.5", home="Los Angeles Lakers", away="Boston Celtics")
        self.assertEqual(
            parsear_pick(p), ("team_periodo", "home|over|mitad|1", 55.5))

    def test_primer_cuarto_total(self):
        self.assertEqual(
            parsear_pick(pick("Primer cuarto total de puntos",
                              "Primer cuarto total de puntos: Over 50.5", "Over 50.5")),
            ("over_under_periodo", "over|cuarto|1", 50.5))

    def test_gana_primera_mitad_no(self):
        self.assertEqual(
            parsear_pick(pick(f"{HOME} gana la primera mitad",
                              "Nacional gana la primera mitad: NO", "No")),
            ("winner_periodo", "no_home|mitad|1", None))

    def test_corners_1a_mitad_sin_datos(self):
        """Corners por periodo: ESPN no los desglosa -> sigue sin datos."""
        self.assertIsNone(
            parsear_pick(pick("Primera mitad tiros de esquina",
                              "Primera mitad tiros de esquina: Over 3.5", "Over 3.5")))

    def test_ambos_marcan_1a_mitad_sin_datos(self):
        self.assertIsNone(
            parsear_pick(pick("Ambos marcan en la primera mitad",
                              "Ambos marcan en la primera mitad: SI", "Si")))

    def test_mercado_normal_no_se_toca(self):
        self.assertEqual(
            parsear_pick(pick("Total de goles", "Total de goles: Over 2.5", "Over 2.5")),
            ("over_under", "over", 2.5))


class TestResolverMitades(unittest.TestCase):
    """rules.resolver_mercado con marcador por periodos."""

    def test_1x2_primera_gana(self):
        self.assertEqual(
            resolver(pick("1a mitad - 1x2", "Nacional 1a mitad - 1x2", "Si"),
                     partido((2, 0), (0, 1))), "ACIERTO")

    def test_1x2_primera_pierde(self):
        self.assertEqual(
            resolver(pick("1a mitad - 1x2", "Nacional 1a mitad - 1x2", "Si"),
                     partido((0, 1), (2, 0))), "FALLO")

    def test_1x2_primera_empate(self):
        self.assertEqual(
            resolver(pick("1a mitad - 1x2", "1a mitad - 1x2", "Empate"),
                     partido((0, 0), (0, 2))), "ACIERTO")

    def test_gana_primera_mitad_no(self):
        # No gana la 1a (0-1): el 'NO' acierta aunque gane el partido.
        self.assertEqual(
            resolver(pick(f"{HOME} gana la primera mitad",
                          "Nacional gana la primera mitad: NO", "No"),
                     partido((0, 1), (2, 0))), "ACIERTO")

    def test_cualquier_mitad_gana_una_perdiendo_el_resto(self):
        self.assertEqual(
            resolver(pick(f"{HOME} gana cualquier mitad",
                          "Nacional gana cualquier mitad: SI", "Si"),
                     partido((1, 0), (0, 3))), "ACIERTO")

    def test_cualquier_mitad_no_gana_ninguna(self):
        self.assertEqual(
            resolver(pick(f"{HOME} gana cualquier mitad",
                          "Nacional gana cualquier mitad: SI", "Si"),
                     partido((0, 0), (0, 0))), "FALLO")

    def test_cualquier_mitad_seleccion_no(self):
        self.assertEqual(
            resolver(pick(f"{HOME} gana cualquier mitad",
                          "Nacional gana cualquier mitad: NO", "No"),
                     partido((1, 0), (0, 0))), "FALLO")

    def test_cualquier_mitad_visitante(self):
        self.assertEqual(
            resolver(pick(f"{AWAY} gana cualquier mitad",
                          "Barranquilla gana cualquier mitad: SI", "Si"),
                     partido((0, 0), (1, 0))), "ACIERTO")

    def test_ambas_mitades_ok(self):
        self.assertEqual(
            resolver(pick(f"{HOME} gana ambas mitades",
                          "Nacional gana ambas mitades: SI", "Si"),
                     partido((1, 1), (0, 0))), "ACIERTO")

    def test_ambas_mitades_solo_una_es_fallo(self):
        self.assertEqual(
            resolver(pick(f"{HOME} gana ambas mitades",
                          "Nacional gana ambas mitades: SI", "Si"),
                     partido((1, 0), (0, 0))), "FALLO")

    def test_over_1a_mitad(self):
        self.assertEqual(
            resolver(pick("Total de goles 1a mitad",
                          "Total de goles 1a mitad: Over 1.5", "Over 1.5"),
                     partido((2, 0), (0, 1))), "ACIERTO")

    def test_under_1a_mitad(self):
        self.assertEqual(
            resolver(pick("Total de goles 1a mitad",
                          "Total de goles 1a mitad: Under 1.5", "Under 1.5"),
                     partido((2, 0), (0, 1))), "FALLO")

    def test_total_2a_mitad_usa_solo_la_segunda(self):
        # 2a mitad: 0-1 = 1 gol -> under 1.5 acierta pese al 2-0 de la 1a.
        self.assertEqual(
            resolver(pick("Total de goles 2a mitad",
                          "Total de goles 2a mitad: Under 1.5", "Under 1.5"),
                     partido((2, 0), (0, 1))), "ACIERTO")

    def test_basket_primera_mitad_suma_cuartos(self):
        # NBA: la 1a mitad es Q1+Q2 (55-40), no el partido entero.
        d = partido((30, 25, 20, 15), (20, 20, 20, 20),
                    home_name="Los Angeles Lakers", away_name="Boston Celtics")
        p = pick("Lakers ganan la primera mitad",
                 "Lakers ganan la primera mitad: SI", "Si",
                 home="Los Angeles Lakers", away="Boston Celtics")
        self.assertEqual(resolver(p, d), "ACIERTO")

    def test_basket_primer_cuarto_total(self):
        d = partido((30, 25, 20, 15), (20, 20, 20, 20))
        p = pick("Primer cuarto total de puntos",
                 "Primer cuarto total de puntos: Over 49.5", "Over 49.5")
        self.assertEqual(resolver(p, d), "ACIERTO")

    def test_basket_total_equipo_2a_mitad(self):
        d = partido((30, 25, 20, 15), (20, 20, 20, 20),
                    home_name="Los Angeles Lakers", away_name="Boston Celtics")
        p = pick("Segunda mitad - Boston Celtics total de puntos",
                 "Segunda mitad - Boston Celtics total de puntos: Over 35.5",
                 "Over 35.5", home="Los Angeles Lakers", away="Boston Celtics")
        # 2a mitad Celtics: 20+20 = 40 > 35.5
        self.assertEqual(resolver(p, d), "ACIERTO")

    def test_sin_periodos_no_resoluble(self):
        """Sin linescores no se inventa nada: NO_RESOLUBLE (queda pendiente)."""
        d = partido((2,), (1,))
        d["home_periodos"] = []
        d["away_periodos"] = []
        self.assertEqual(
            resolver(pick("1a mitad - 1x2", "Nacional 1a mitad - 1x2", "Si"), d),
            "NO_RESOLUBLE")

    def test_prorroga_no_cuenta_como_mitad(self):
        # Futbol con prorroga: linescores [1a, 2a, ET]. Solo cuentan las 2 mitades.
        d = partido((0, 0, 1), (0, 0, 0))
        self.assertEqual(
            resolver(pick(f"{HOME} gana cualquier mitad",
                          "Nacional gana cualquier mitad: SI", "Si"), d),
            "FALLO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
