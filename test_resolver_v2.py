# -*- coding: utf-8 -*-
"""Tests del resolutor v2 (data_engine/resolver.py).

Cada test corresponde a un bug real medido en produccion.
"""
import unittest

from data_engine import resolver


def detalle(local="Local", visita="Visitante", gl=0, gv=0,
            periodo_local=None, periodo_visitante=None, invertido=False):
    """Detalle con el mismo contrato que ESPN (`homeAway` + `linescores`)."""
    h = {"name": local, "score": gl, "homeAway": "home",
         "linescores": [{"value": v} for v in (periodo_local or [])]}
    a = {"name": visita, "score": gv, "homeAway": "away",
         "linescores": [{"value": v} for v in (periodo_visitante or [])]}
    teams = [a, h] if invertido else [h, a]
    return {"state": "post", "teams": teams}


def pick(market, selection, titulo=None):
    return {"market": market, "titulo": titulo or market, "selection": selection}


class TestUnderOver(unittest.TestCase):
    """Bug 1: 'menos de'.startswith('m') -> todos los under salian como over."""

    def test_under_ganado_no_se_marca_fallo(self):
        # Under 2.5 con 1-0 (1 gol): el pick era VERDADERO.
        r = resolver.resolver_mercado(pick("Under 2.5 goles", "Under 2.5"), detalle(gl=1, gv=0))
        self.assertEqual(r, "ACIERTO")

    def test_under_perdido_no_se_marca_acierto(self):
        # Under 2.5 con 2-1 (3 goles): el pick era FALSO.
        r = resolver.resolver_mercado(pick("Under 2.5 goles", "Under 2.5"), detalle(gl=2, gv=1))
        self.assertEqual(r, "FALLO")

    def test_over_sigue_bien(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("Over 2.5 goles", "Over 2.5"), detalle(gl=2, gv=1)),
            "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(pick("Over 2.5 goles", "Over 2.5"), detalle(gl=1, gv=0)),
            "FALLO")

    def test_menos_de_en_espanol(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("Menos de 3.5 goles", "Menos de 3.5"),
                                      detalle(gl=2, gv=1)),
            "ACIERTO")

    def test_total_de_un_equipo(self):
        r = resolver.resolver_mercado(
            pick("Total de goles Local Over 0.5", "Local Over 0.5"),
            detalle(local="Local", gl=2, gv=0))
        self.assertEqual(r, "ACIERTO")


class TestPush(unittest.TestCase):
    """Bug 3: la linea exacta devolvia FALLO en ambos sentidos."""

    def test_push_en_total_es_anulado(self):
        r = resolver.resolver_mercado(pick("Over 2 goles", "Over 2"), detalle(gl=1, gv=1))
        self.assertEqual(r, "ANULADO")
        r = resolver.resolver_mercado(pick("Under 2 goles", "Under 2"), detalle(gl=1, gv=1))
        self.assertEqual(r, "ANULADO")

    def test_push_en_handicap_es_anulado(self):
        r = resolver.resolver_mercado(
            pick("Handicap Local (-1)", "Local (-1)"), detalle(gl=2, gv=1))
        self.assertEqual(r, "ANULADO")


class TestHandicap(unittest.TestCase):
    """Bug 2: no habia NINGUNA rama de handicap (nunca se resolvian)."""

    def test_handicap_negativo_gana(self):
        r = resolver.resolver_mercado(
            pick("Handicap Local (-1.5)", "Local (-1.5)"), detalle(gl=3, gv=1))
        self.assertEqual(r, "ACIERTO")

    def test_handicap_negativo_pierde(self):
        r = resolver.resolver_mercado(
            pick("Handicap Local (-1.5)", "Local (-1.5)"), detalle(gl=2, gv=1))
        self.assertEqual(r, "FALLO")

    def test_handicap_positivo_visitante(self):
        r = resolver.resolver_mercado(
            pick("Handicap Visitante (+1.5)", "Visitante (+1.5)"), detalle(gl=2, gv=1))
        self.assertEqual(r, "ACIERTO")


class TestMitades(unittest.TestCase):
    """Bug 4: se leian por indice, ignorando homeAway."""

    def test_mitades_con_array_invertido(self):
        # ESPN devuelve primero al visitante; el local gano las dos mitades.
        d = detalle(gl=2, gv=0, periodo_local=[1, 1], periodo_visitante=[0, 0],
                    invertido=True)
        r = resolver.resolver_mercado(
            pick("Local gana ambas mitades", "Local"), d)
        self.assertEqual(r, "ACIERTO")

    def test_primera_mitad_empate_es_anulado(self):
        d = detalle(gl=1, gv=1, periodo_local=[0, 1], periodo_visitante=[0, 1])
        r = resolver.resolver_mercado(
            pick("1a mitad - 1x2", "Local"), d)
        self.assertEqual(r, "ANULADO")


class TestOtrosMercados(unittest.TestCase):

    def test_btts_no(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("Ambos equipos marcan", "NO"), detalle(gl=1, gv=0)),
            "ACIERTO")

    def test_btts_si(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("Ambos equipos marcan", "SI"), detalle(gl=1, gv=1)),
            "ACIERTO")

    def test_ganador_con_empate_es_fallo(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("Ganador", "Local"), detalle(gl=1, gv=1)),
            "FALLO")

    def test_doble_oportunidad_empate(self):
        self.assertEqual(
            resolver.resolver_mercado(
                pick("Doble oportunidad", "Empate o Visitante"), detalle(gl=1, gv=1)),
            "ACIERTO")

    def test_estadisticas_no_se_deciden_con_el_marcador(self):
        self.assertIsNone(
            resolver.resolver_mercado(
                pick("Tarjetas totales Over 2.5", "Over 2.5"), detalle(gl=3, gv=0)))

    def test_nombres_solapados(self):
        # 'Real Madrid' NO debe coincidir con 'Real Madrid Castilla'.
        d = detalle(local="Real Madrid", visita="Real Madrid Castilla", gl=2, gv=0)
        r = resolver.resolver_mercado(pick("Ganador", "Real Madrid Castilla"), d)
        self.assertEqual(r, "FALLO")


class TestGanadorYMitades(unittest.TestCase):
    """Mercados que el book publica y el resolver no reconocia."""

    def test_1x2_es_ganador(self):
        self.assertEqual(
            resolver.resolver_mercado(pick("1x2", "Local"), detalle(gl=2, gv=1)), "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(pick("1x2", "Visitante"), detalle(gl=2, gv=1)), "FALLO")

    def test_segunda_mitad(self):
        d = detalle(gl=2, gv=1, periodo_local=[0, 2], periodo_visitante=[1, 0])
        self.assertEqual(
            resolver.resolver_mercado(pick("2ª Mitad - 1x2", "Local"), d), "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(pick("2ª Mitad - 1x2", "Visitante"), d), "FALLO")

    def test_mitad_con_handicap_no_se_inventa(self):
        """Hándicap/doble oportunidad de mitad: PENDIENTE, nunca un veredicto falso."""
        d = detalle(gl=2, gv=1, periodo_local=[0, 2], periodo_visitante=[1, 0])
        self.assertIsNone(
            resolver.resolver_mercado(pick("2ª Mitad - hándicap 1X2", "Local"), d))
        self.assertIsNone(
            resolver.resolver_mercado(pick("2ª Mitad - doble oportunidad", "Local"), d))
        self.assertIsNone(
            resolver.resolver_mercado(pick("2ª Mitad - multigoles", "1-5"), d))

    def test_primera_mitad_respeta_el_lado(self):
        # El VISITANTE gano la primera mitad: antes se asumia local y salia FALLO.
        d = detalle(gl=2, gv=1, periodo_local=[0, 2], periodo_visitante=[1, 0])
        self.assertEqual(
            resolver.resolver_mercado(pick("1ª mitad - 1x2", "Visitante"), d), "ACIERTO")


class TestTenis(unittest.TestCase):
    """Tenis: el 'marcador' son sets y juegos (linescores), no goles."""

    def _d(self, sets_h, sets_a, estado="Final"):
        return {"state": "post", "estado_detallado": estado, "teams": [
            {"name": "Holger Rune", "score": 2 if sets_h[0] > sets_a[0] else 0,
             "homeAway": "home", "winner": sets_h[0] > sets_a[0],
             "linescores": [{"value": v} for v in sets_h]},
            {"name": "Zizou Bergs", "score": 2 if sets_a[0] > sets_h[0] else 0,
             "homeAway": "away", "winner": sets_a[0] > sets_h[0],
             "linescores": [{"value": v} for v in sets_a]},
        ]}

    def _pick(self, market, selection):
        return {"sport": "tennis", "market": market, "titulo": market,
                "selection": selection}

    def test_ganador_por_bandera(self):
        d = self._d([6, 6], [3, 4])
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Ganador", "Holger Rune"), d), "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Ganador", "Zizou Bergs"), d), "FALLO")

    def test_total_de_juegos(self):
        d = self._d([6, 6], [3, 4])  # 19 juegos
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Total juegos", "Under 22.5"), d), "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Total juegos", "Over 22.5"), d), "FALLO")

    def test_handicap_de_juegos(self):
        d = self._d([6, 6], [3, 4])  # +5 juegos para Rune
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Handicap de juegos", "Holger Rune -2.5"), d),
            "ACIERTO")
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Handicap de juegos", "Zizou Bergs +2.5"), d),
            "FALLO")

    def test_primer_set(self):
        d = self._d([6, 4], [7, 6])  # Bergs gana el primero
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Primer set", "Zizou Bergs"), d), "ACIERTO")

    def test_retirada_anula_totales(self):
        d = self._d([6, 1], [3, 4], estado="Retired")
        self.assertEqual(
            resolver.resolver_mercado(self._pick("Total juegos", "Over 22.5"), d), "ANULADO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
