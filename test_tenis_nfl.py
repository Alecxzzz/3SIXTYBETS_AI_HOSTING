# -*- coding: utf-8 -*-
"""
Test de mercados REALES de tenis y NFL en Doradobet.

Por que existe: se concluyo que 'el bookmaker no cubre el tenis' porque se
buscaba solo en los sportIds 77/78 (Challenger/ITF) y no en el 68 (ATP/WTA).
Con el 68 hay 116 tenistas de la ventana de ESPN y 50 de 80 partidos con
cuotas reales. Este test fija ese descubrimiento para que no se revierta.

Consulta el bookmaker de verdad: si el formato del payload cambia, falla.
"""
import sys
import io
import unittest

import cuotas_doradobet as D
import dashboard as d

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _skip_sin_libro(sport, ev):
    if not ev:
        self_skip = f"el bookmaker no tiene eventos de {sport} ahora mismo"
        raise unittest.SkipTest(self_skip)


class TestSportIds(unittest.TestCase):
    """Los sportIds se verificaron uno a uno mirando los equipos de ejemplo."""

    def test_nba_no_es_nfl(self):
        # Regresion: se asigno nba=75, que en realidad es NFL (Browns/Steelers).
        self.assertNotEqual(D.DORADO_SPORT_IDS_LIST["nba"], [75])
        self.assertEqual(D.DORADO_SPORT_IDS["nfl"], 75)

    def test_tenis_incluye_el_circuito_mayor(self):
        """El 68 es ATP/WTA. Sin el, el tenis no encuentra nada."""
        self.assertIn(68, D.DORADO_SPORT_IDS_LIST["tennis"])

    def test_todos_los_sportids_existen(self):
        for sport, ids in D.DORADO_SPORT_IDS_LIST.items():
            for sid in ids:
                data = D._get_events_sport_id(sid)
                self.assertIsNotNone(data, f"sportId {sid} ({sport}) no responde")
                self.assertTrue(data.get("events"), f"sportId {sid} ({sport}) sin eventos")


class TestFusionSportIds(unittest.TestCase):
    def test_tenis_fusiona_los_tres_ids(self):
        data = D.get_events_deporte("tennis")
        n = len(data.get("events") or [])
        # Solo el 68 ya tiene ~390; fusionados tienen que ser mas.
        self.assertGreater(n, 380, "no se estan combinando los sportIds del tenis")

    def test_no_hay_eventos_duplicados(self):
        data = D.get_events_deporte("tennis")
        ids = [e.get("id") for e in (data.get("events") or [])]
        self.assertEqual(len(ids), len(set(ids)), "eventos duplicados al fusionar")


class TestTenisConCuotas(unittest.TestCase):
    def test_hay_tenistas_de_la_ventana_de_espn(self):
        """116 coincidencias: el libro SI cubre el circuito mayor."""
        ps = [p for p in d._partidos_hoy() if p["sport"] == "tennis"]
        if not ps:
            self.skipTest("no hay partidos de tenis en la ventana ahora")
        data = D.get_events_deporte("tennis")
        comps = {c.get("id"): c for c in (data.get("competitors") or [])}
        libro = set()
        for e in data.get("events") or []:
            for i in (e.get("competitorIds") or []):
                n = (comps.get(i) or {}).get("name")
                if n:
                    libro.add(n.strip())
        espn = set()
        for p in ps:
            espn.add((p["home_name"] or "").strip())
            espn.add((p["away_name"] or "").strip())
        match = [n for n in espn if any(D._coincide_equipo(b, n) for b in libro)]
        self.assertGreater(
            len(match), 20,
            f"solo {len(match)} tenistas de ESPN aparecen en el libro",
        )

    def test_algun_partido_tiene_mercados_con_cuota(self):
        ps = [p for p in d._partidos_hoy() if p["sport"] == "tennis"]
        if not ps:
            self.skipTest("sin partidos de tenis en la ventana")
        con = 0
        for p in ps[:12]:
            ms = D.mercados_reales("tennis", p["home_name"], p["away_name"], p["date"], 1.20, 2.50)
            if ms:
                con += 1
                for m in ms:
                    self.assertTrue(1.20 <= m["odds"] <= 2.50, f"cuota fuera de rango: {m['odds']}")
        self.assertGreater(con, 0, "ningun partido de tenis devolvio mercados con cuota")


class TestNFL(unittest.TestCase):
    def test_nfl_tiene_eventos_y_mercados(self):
        data = D.get_events_deporte("nfl")
        evs = data.get("events") or []
        if not evs:
            self.skipTest("el bookmaker no tiene NFL ahora mismo")
        comps = {c.get("id"): c for c in (data.get("competitors") or [])}
        e = evs[0]
        nombres = [(comps.get(i) or {}).get("name") or "" for i in (e.get("competitorIds") or [])]
        nombres = [" ".join(n.split()) for n in nombres]
        ms = D.mercados_reales("nfl", nombres[1], nombres[0], e.get("startDate"), 1.20, 2.50)
        self.assertGreater(len(ms), 10, f"NFL solo devolvio {len(ms)} mercados")

    def test_nombres_sin_tabs(self):
        """'WAS Commanders\\t\\t' rompia el emparejamiento."""
        self.assertEqual(D._norm("WAS Commanders\t\t"), "was commanders")

    def test_nfl_tiene_ganador_y_handicap(self):
        data = D.get_events_deporte("nfl")
        evs = data.get("events") or []
        if not evs:
            self.skipTest("sin NFL ahora mismo")
        comps = {c.get("id"): c for c in (data.get("competitors") or [])}
        e = evs[0]
        n = [" ".join(((comps.get(i) or {}).get("name") or "").split())
             for i in (e.get("competitorIds") or [])]
        ms = D.mercados_reales("nfl", n[1], n[0], e.get("startDate"), 1.20, 2.50)
        # El bookmaker escribe 'Hándicap' con tilde: se normaliza antes
        # de comparar, o la comprobacion falla solo por el acento.
        nombres = d._norm_mercado(" ".join(m["market"] for m in ms))
        self.assertIn("ganador", nombres)
        self.assertIn("handicap", nombres)
        self.assertIn("mitad", nombres)


class TestCatalogoUnico(unittest.TestCase):
    """El catalogo que comparten backend y frontend."""

    def test_catalogo_cubre_todos_los_deportes(self):
        cat = d.catalogo_mercados()
        for sport in d.MERCADOS_POR_DEPORTE:
            self.assertIn(sport, cat)
            self.assertGreater(len(cat[sport]["mercados"]), 0, f"{sport} sin mercados")

    def test_todo_lo_que_ofrece_es_resoluble(self):
        """Si algo sale en el catalogo, el resolver lo sabe comprobar."""
        for sport, data in d.catalogo_mercados().items():
            for item in data["mercados"]:
                self.assertTrue(
                    d.mercado_resoluble(item["mercado"]),
                    f"{sport}: '{item['mercado']}' se ofrece pero no se puede resolver",
                )
                self.assertGreater(item["peso"], 0)

    def test_cada_mercado_va_con_efectividad(self):
        for sport, data in d.catalogo_mercados().items():
            for item in data["mercados"]:
                self.assertIn("efectividad", item)

    def test_ordenado_por_peso(self):
        for sport, data in d.catalogo_mercados().items():
            pesos = [i["peso"] for i in data["mercados"]]
            self.assertEqual(pesos, sorted(pesos, reverse=True), f"{sport} sin ordenar")


class TestMercadosDeOtroDeporte(unittest.TestCase):
    def test_ningun_mercado_es_de_otro_deporte(self):
        for sport, data in d.catalogo_mercados().items():
            for item in data["mercados"]:
                self.assertFalse(
                    d.mercado_de_otro_deporte(item["mercado"], sport),
                    f"{sport}: '{item['mercado']}' pertenece a otro deporte",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
