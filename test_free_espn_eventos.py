# -*- coding: utf-8 -*-
"""
Test de la PROGRAMACION de partidos de /espn-gratis (FREE_ESPN_EVENTOS).

Lo que se protege:
- Mientras Chelsea vs Manchester United esta en vivo, ese manda aunque
  Manchester City vs Brighton ya este programado debajo.
- Cuando el Chelsea termina (state = "post"), la pagina pasa SOLA al
  siguiente partido, sin tocar codigo ni la BD.
- Sin partidos abiertos todavia, se muestra el proximo (cuenta atras).
- Con todos cerrados, queda el mas reciente.
- La agenda se consulta POR LIGA: Premier League (eng.1) y Bundesliga (ger.1).
- Cada partido usa SU propia señal ("stream" del evento), y con
  FREE_ESPN_STREAM_UNICO = True el endpoint devuelve UNA sola opcion.
"""
import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import main
import sports


CHELSEA = {
    "name": "Manchester United at Chelsea",
    "date": "2026-10-31T12:30Z",
    "state": "in",
    "home": {"name": "Chelsea"},
    "away": {"name": "Manchester United"},
}
CITY = {
    "name": "Brighton & Hove Albion at Manchester City",
    "date": "2026-10-31T15:00Z",
    "state": "pre",
    "home": {"name": "Manchester City"},
    "away": {"name": "Brighton & Hove Albion"},
}
BAYERN = {
    "name": "Borussia Dortmund at Bayern Munich",
    "date": "2026-10-31T17:30Z",
    "state": "pre",
    "home": {"name": "Bayern Munich"},
    "away": {"name": "Borussia Dortmund"},
}


def _utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


@contextlib.contextmanager
def _agenda(estado_chelsea="in", estado_city="pre", estado_bayern="pre"):
    """Sustituye la agenda de ESPN: cada liga con sus partidos del dia."""
    consultadas = []

    def _fake_get_sport_games(sport, league=None):
        consultadas.append(league)
        if league == "ger.1":
            return {"games": [dict(BAYERN, state=estado_bayern)]}
        return {
            "games": [
                dict(CHELSEA, state=estado_chelsea),
                dict(CITY, state=estado_city),
            ]
        }

    with mock.patch.object(sports, "get_sport_games", _fake_get_sport_games):
        yield consultadas


class TestSeleccionDelPartido(unittest.TestCase):
    """_free_espn_buscar_partido elige bien segun la hora y el estado."""

    def test_en_vivo_manda_el_chelsea(self):
        with _agenda(estado_chelsea="in", estado_city="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T12:50Z"))
        self.assertEqual(game["name"], CHELSEA["name"])

    def test_pasa_al_city_cuando_el_chelsea_termina(self):
        with _agenda(estado_chelsea="post", estado_city="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T14:50Z"))
        self.assertEqual(game["name"], CITY["name"])

    def test_city_en_vivo(self):
        with _agenda(estado_chelsea="post", estado_city="in"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T15:20Z"))
        self.assertEqual(game["name"], CITY["name"])

    def test_sin_ventana_abierta_muestra_el_proximo(self):
        with _agenda(estado_chelsea="pre", estado_city="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T11:00Z"))
        self.assertEqual(game["name"], CHELSEA["name"])

    def test_todos_cerrados_queda_el_mas_reciente(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="post"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T21:00Z"))
        # Cierres: Chelsea 17:30Z, City 20:00Z, Bayern 22:30Z -> gana el Bayern.
        self.assertEqual(game["name"], BAYERN["name"])

    def test_pasa_al_bayern_cuando_termina_el_city(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T17:30Z"))
        self.assertEqual(game["name"], BAYERN["name"])

    def test_bayern_en_vivo(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="in"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T18:00Z"))
        self.assertEqual(game["name"], BAYERN["name"])

    def test_consulta_la_liga_de_cada_evento(self):
        with _agenda(estado_chelsea="post", estado_city="post") as consultadas:
            main._free_espn_buscar_partido(ahora=_utc("2026-10-31T17:30Z"))
        self.assertIn("eng.1", consultadas)  # Premier League
        self.assertIn("ger.1", consultadas)  # Bundesliga

    def test_sin_partidos_programados_devuelve_none(self):
        with _agenda() as _:
            with mock.patch.object(main, "FREE_ESPN_EVENTOS", []):
                game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T12:50Z"))
        self.assertIsNone(game)


class TestSenalPorPartido(unittest.TestCase):
    """_free_espn_stream_de devuelve el m3u8 del evento que toca."""

    def test_usa_el_stream_propio_de_cada_evento(self):
        eventos = [
            {"match": ("chelsea", "manchester united"), "stream": "http://senal/chelsea.m3u8"},
            {"match": ("manchester city", "brighton"), "stream": "http://senal/city.m3u8"},
            {"match": ("bayern", "dortmund"), "league": "ger.1", "stream": "http://senal/bayern.m3u8"},
        ]
        with mock.patch.object(main, "FREE_ESPN_EVENTOS", eventos):
            self.assertEqual(main._free_espn_stream_de(CHELSEA["name"]), "http://senal/chelsea.m3u8")
            self.assertEqual(main._free_espn_stream_de(CITY["name"]), "http://senal/city.m3u8")
            self.assertEqual(main._free_espn_stream_de(BAYERN["name"]), "http://senal/bayern.m3u8")

    def test_evento_sin_stream_usa_el_principal(self):
        eventos = [{"match": ("manchester city", "brighton")}]
        with mock.patch.object(main, "FREE_ESPN_EVENTOS", eventos):
            self.assertEqual(
                main._free_espn_stream_de(CITY["name"]),
                main.FREE_ESPN_STREAM_PRINCIPAL,
            )

    def test_partido_no_programado_usa_el_principal(self):
        self.assertEqual(
            main._free_espn_stream_de("Fulham at Aston Villa"),
            main.FREE_ESPN_STREAM_PRINCIPAL,
        )


class TestEndpointStream(unittest.TestCase):
    """Con señal unica, /tv-live/stream entrega una sola opcion del partido."""

    def test_una_sola_opcion_con_el_stream_del_partido(self):
        from fastapi.testclient import TestClient

        ahora = datetime.now(timezone.utc)
        ventana = (ahora - timedelta(minutes=5), ahora + timedelta(minutes=60))
        with mock.patch.object(main, "_free_espn_buscar_partido", return_value=CITY), \
                mock.patch.object(main, "_free_espn_ventana", return_value=ventana), \
                mock.patch.object(main, "get_pagadito_plans", return_value=[]):
            resp = TestClient(main.app).get("/tv-live/stream")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["opciones"]), 1)
        self.assertEqual(data["opciones"][0]["label"], "Opción 1")
        self.assertIn(main._free_espn_stream_de(CITY["name"]).replace(":", "%3A").replace("/", "%2F"),
                      data["opciones"][0]["url"])
        self.assertGreater(data["tiempo_restante_seg"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
