# -*- coding: utf-8 -*-
"""Tests del resolutor de TENIS (data_engine/tennis.py).

Cubren los casos que en produccion se marcaban mal: sets/juegos, push,
retirada, walkover y el orden local/visitante invertido de ESPN.
"""
import unittest

from data_engine import tennis


def det(gl=2, gv=0, sets_h=(6, 6), sets_a=(4, 4), estado="STATUS_FINAL",
        home="Holger Rune", away="Zizou Bergs", invertido=False):
    h = {"nombre": home, "lado": "home", "score": float(gl),
         "juegos": list(sets_h), "juegos_total": float(sum(sets_h)),
         "linescores": [], "winner": gl > gv}
    a = {"nombre": away, "lado": "away", "score": float(gv),
         "juegos": list(sets_a), "juegos_total": float(sum(sets_a)),
         "linescores": [], "winner": gv > gl}
    teams = [a, h] if invertido else [h, a]
    return {"estado": "post", "estado_detallado": estado, "teams": teams, "nota": ""}


def pick(market, selection, titulo=None):
    return {"sport": "tennis", "market": market,
            "titulo": titulo or market, "selection": selection}


class TestGanador(unittest.TestCase):

    def test_ganador_local(self):
        self.assertEqual(
            tennis.resolver(pick("Ganador", "Holger Rune"), det()), "ACIERTO")

    def test_ganador_visitante(self):
        self.assertEqual(
            tennis.resolver(pick("Ganador", "Zizou Bergs"), det()), "FALLO")

    def test_orden_invertido_no_invierte_el_veredicto(self):
        # Antes: ESPN mandaba al visitante primero y el pick salia al reves.
        d = det(gl=2, gv=0, invertido=True)
        self.assertEqual(tennis.resolver(pick("Ganador", "Holger Rune"), d), "ACIERTO")

    def test_ganador_en_retirada_se_paga(self):
        d = det(gl=1, gv=0, sets_h=(6, 1), sets_a=(3, 4), estado="STATUS_RETIRED")
        self.assertEqual(tennis.resolver(pick("Ganador", "Holger Rune"), d), "ACIERTO")


class TestJuegos(unittest.TestCase):

    def test_total_juegos_under_gana(self):
        # 6-4 6-4 = 20 juegos; Under 22.5 -> ACIERTO.
        d = det(sets_h=(6, 6), sets_a=(4, 4))
        self.assertEqual(
            tennis.resolver(pick("Total juegos (under/over)", "Under 22.5"), d),
            "ACIERTO")

    def test_total_juegos_over_pierde(self):
        d = det(sets_h=(6, 6), sets_a=(4, 4))
        self.assertEqual(
            tennis.resolver(pick("Total juegos (under/over)", "Over 22.5"), d),
            "FALLO")

    def test_tres_sets_31_juegos(self):
        # 6-3 3-6 7-6 = 9 + 9 + 13 = 31.
        d = det(gl=2, gv=1, sets_h=(6, 3, 7), sets_a=(3, 6, 6))
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Over 30.5"), d), "ACIERTO")
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Under 30.5"), d), "FALLO")

    def test_push_es_anulado(self):
        # 20 juegos exactos: linea 20 -> devolucion, NO fallo.
        d = det(sets_h=(6, 6), sets_a=(4, 4))
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Over 20"), d), "ANULADO")
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Under 20"), d), "ANULADO")

    def test_handicap_juegos(self):
        # 12-8 = +4; Rune -3.5 -> ACIERTO.
        d = det(sets_h=(6, 6), sets_a=(4, 4))
        self.assertEqual(
            tennis.resolver(pick("Handicap de juego", "Holger Rune (-3.5)"), d),
            "ACIERTO")
        self.assertEqual(
            tennis.resolver(pick("Handicap de juego", "Holger Rune (-4.5)"), d),
            "FALLO")


class TestSets(unittest.TestCase):

    def test_handicap_sets(self):
        d = det(gl=2, gv=1, sets_h=(6, 3, 7), sets_a=(3, 6, 6))
        # 2-1 en sets = margen +1: con -0.5 gana, con -1.5 pierde.
        self.assertEqual(
            tennis.resolver(pick("Hándicap de Set", "Holger Rune (-0.5)"), d),
            "ACIERTO")
        self.assertEqual(
            tennis.resolver(pick("Hándicap de Set", "Holger Rune (-1.5)"), d),
            "FALLO")

    def test_primer_set(self):
        d = det(gl=2, gv=1, sets_h=(6, 3, 7), sets_a=(3, 6, 6))
        self.assertEqual(
            tennis.resolver(pick("Ganador del primer set", "Holger Rune"), d),
            "ACIERTO")

    def test_sets_exactos(self):
        d = det(gl=2, gv=0)
        self.assertEqual(
            tennis.resolver(pick("Resultado exacto en sets", "2-0"), d), "ACIERTO")
        self.assertEqual(
            tennis.resolver(pick("Resultado exacto en sets", "2-1"), d), "FALLO")


class TestAnulados(unittest.TestCase):

    def test_retirada_anula_totales(self):
        d = det(gl=1, gv=0, sets_h=(6, 1), sets_a=(3, 4), estado="STATUS_RETIRED")
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Over 15.5"), d), "ANULADO")

    def test_walkover_anula_totales(self):
        d = det(gl=1, gv=0, sets_h=(), sets_a=(), estado="STATUS_WALKOVER")
        self.assertEqual(
            tennis.resolver(pick("Total juegos", "Under 22.5"), d), "ANULADO")

    def test_partido_en_juego_no_se_resuelve(self):
        d = det()
        d["estado"] = "in"
        self.assertIsNone(tennis.resolver(pick("Ganador", "Holger Rune"), d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
