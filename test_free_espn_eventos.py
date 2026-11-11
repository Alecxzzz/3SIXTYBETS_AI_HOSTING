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
- Cada partido usa SUS propias señales: "stream" (una) u "opciones" (varias,
  p.ej. Opción 1 ESPN Colombia + Opción 2 Portugués). Con
  FREE_ESPN_OPCIONES_PROGRAMADAS = True el endpoint devuelve exactamente esas.
- Una señal DASH (.mpd) sale SIN pasar por /hls-proxy (el proxy solo reescribe
  HLS), marcada con "dash": True.
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
INTER = {
    "name": "Internazionale at AC Milan",
    "date": "2026-10-31T19:45Z",
    "state": "pre",
    "home": {"name": "AC Milan"},
    "away": {"name": "Internazionale"},
}
NUGGETS = {
    "name": "Denver Nuggets at Houston Rockets",
    "date": "2026-11-10T01:30Z",
    "state": "in",
    "home": {"name": "Houston Rockets"},
    "away": {"name": "Denver Nuggets"},
}
MPD = ("https://otte.cache.aiv-cdn.net/bom-nitro/live/clients/dash/enc/cjglydxghe/"
       "out/v1/8977baf175da4b94873194613dd3fe55/cenc.mpd?ck=token")
# 76ers vs Celtics: la PRINCIPAL esta atada a la IP del cliente (token + ?ip=)
SIXERS = {
    "name": "Boston Celtics at Philadelphia 76ers",
    "date": "2026-11-11T01:00Z",
    "state": "in",
    "home": {"name": "Philadelphia 76ers"},
    "away": {"name": "Boston Celtics"},
}
KHALA = ("https://khala.futlivehd.com/max1/index.m3u8?ip=190.212.210.156"
         "&token=0754be40fa1a61f2880cbb08762fd9fe1623a543-e9-1794397221-1794343221")
CLAVE_76 = "36a385124dcdfb2f0969935065e03afa:ccecd65514dff22eae493b975ccf9a9e"


def _utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


# Programacion de prueba: los tests NO dependen de la lista real de produccion
# (FREE_ESPN_EVENTOS cambia cada jornada); se inyecta esta en _agenda().
EVENTOS_PRUEBA = [
    {"match": ("chelsea", "manchester united")},
    {"match": ("manchester city", "brighton")},
    {"match": ("bayern", "dortmund"), "league": "ger.1"},
    {"match": ("inter", "milan"), "league": "ita.1"},
]


@contextlib.contextmanager
def _agenda(estado_chelsea="in", estado_city="pre", estado_bayern="pre", estado_inter="pre"):
    """Sustituye la agenda de ESPN: cada liga con sus partidos del dia."""
    consultadas = []

    def _fake_get_sport_games(sport, league=None):
        consultadas.append(league)
        if league == "ger.1":
            return {"games": [dict(BAYERN, state=estado_bayern)]}
        if league == "ita.1":
            return {"games": [dict(INTER, state=estado_inter)]}
        return {
            "games": [
                dict(CHELSEA, state=estado_chelsea),
                dict(CITY, state=estado_city),
            ]
        }

    with mock.patch.object(sports, "get_sport_games", _fake_get_sport_games), \
            mock.patch.object(main, "FREE_ESPN_EVENTOS", EVENTOS_PRUEBA):
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
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="post", estado_inter="post"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T21:00Z"))
        # Cierres: Chelsea 17:30Z, City 20:00Z, Bayern 22:30Z, Inter 00:45Z -> gana el Inter.
        self.assertEqual(game["name"], INTER["name"])

    def test_pasa_al_bayern_cuando_termina_el_city(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T17:30Z"))
        self.assertEqual(game["name"], BAYERN["name"])

    def test_bayern_en_vivo(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="in"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T18:00Z"))
        self.assertEqual(game["name"], BAYERN["name"])

    def test_pasa_al_inter_milan_cuando_termina_el_bayern(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="post", estado_inter="pre"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T19:45Z"))
        self.assertEqual(game["name"], INTER["name"])

    def test_inter_milan_en_vivo(self):
        with _agenda(estado_chelsea="post", estado_city="post", estado_bayern="post", estado_inter="in"):
            game = main._free_espn_buscar_partido(ahora=_utc("2026-10-31T20:30Z"))
        self.assertEqual(game["name"], INTER["name"])

    def test_consulta_la_liga_de_cada_evento(self):
        with _agenda(estado_chelsea="post", estado_city="post") as consultadas:
            main._free_espn_buscar_partido(ahora=_utc("2026-10-31T17:30Z"))
        self.assertIn("eng.1", consultadas)  # Premier League
        self.assertIn("ger.1", consultadas)  # Bundesliga
        self.assertIn("ita.1", consultadas)  # Serie A

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

    def test_senal_por_defecto_es_espn_3(self):
        self.assertIn("38.252.215.33:8000/play/a00l", main.FREE_ESPN_STREAM_PRINCIPAL)

    def test_inter_milan_usa_espn_3(self):
        self.assertEqual(
            main._free_espn_opciones_de(INTER["name"]),
            [{"label": "", "url": main.FREE_ESPN_STREAM_PRINCIPAL, "referer": "", "embed": False}],
        )

    def test_referer_por_host_de_la_senal(self):
        self.assertEqual(
            main._free_espn_referer_de("https://barinaslive1.boldmss.com/ESPN_3/x.m3u8"),
            "https://barinaslive1.boldmss.com/",
        )
        self.assertEqual(main._free_espn_referer_de("http://38.252.215.33:8000/play/a024/index.m3u8"), "")

    def test_evento_con_varias_senales_devuelve_todas(self):
        eventos = [{
            "match": ("bayern", "dortmund"),
            "opciones": [
                {"label": "(ESPN 3)", "url": "http://senal/espn3.m3u8"},
                {"label": "(Portugués)", "url": "http://senal/pt.m3u8"},
            ],
        }]
        with mock.patch.object(main, "FREE_ESPN_EVENTOS", eventos):
            opciones = main._free_espn_opciones_de(BAYERN["name"])
            principal = main._free_espn_stream_de(BAYERN["name"])
        self.assertEqual([o["label"] for o in opciones], ["(ESPN 3)", "(Portugués)"])
        self.assertEqual(principal, "http://senal/espn3.m3u8")

    def test_evento_con_una_senal_devuelve_una_opcion_sin_label(self):
        opciones = main._free_espn_opciones_de(CITY["name"])
        self.assertEqual(len(opciones), 1)
        self.assertEqual(opciones[0]["label"], "")

    def test_opciones_sin_url_valida_se_ignoran(self):
        eventos = [{
            "match": ("bayern", "dortmund"),
            "opciones": [
                {"label": "(mala)", "url": ""},
                {"label": "(buena)", "url": "http://senal/buena.m3u8"},
            ],
        }]
        with mock.patch.object(main, "FREE_ESPN_EVENTOS", eventos):
            opciones = main._free_espn_opciones_de(BAYERN["name"])
        self.assertEqual(
            opciones,
            [{"label": "(buena)", "url": "http://senal/buena.m3u8", "referer": "", "embed": False}],
        )


class TestSenalDash(unittest.TestCase):
    """Una señal DASH (.mpd) NO es un m3u8 ni una pagina embed."""

    def test_detecta_manifest_dash(self):
        self.assertTrue(main._free_espn_es_dash(MPD))
        self.assertFalse(main._free_espn_es_dash("http://senal/espn3.m3u8"))
        self.assertFalse(main._free_espn_es_dash(""))

    def test_senal_dash_marcada_embed_false_no_va_por_iframe(self):
        eventos = [{
            "sport": "nba",
            "match": ("nuggets", "rockets"),
            "opciones": [{"label": "STREAM 1", "url": MPD, "embed": False}],
        }]
        with mock.patch.object(main, "FREE_ESPN_EVENTOS", eventos):
            opciones = main._free_espn_opciones_de(NUGGETS["name"])
        self.assertEqual(opciones, [{"label": "STREAM 1", "url": MPD, "referer": "", "embed": False}])


class TestEndpointStream(unittest.TestCase):
    """Con señales programadas, /tv-live/stream entrega una opcion por señal."""

    def _get_stream(self, game, extra_eventos=None):
        from fastapi.testclient import TestClient

        ahora = datetime.now(timezone.utc)
        ventana = (ahora - timedelta(minutes=5), ahora + timedelta(minutes=60))
        parches = [
            mock.patch.object(main, "_free_espn_buscar_partido", return_value=game),
            mock.patch.object(main, "_free_espn_ventana", return_value=ventana),
            mock.patch.object(main, "get_pagadito_plans", return_value=[]),
        ]
        if extra_eventos is not None:
            parches.append(mock.patch.object(main, "FREE_ESPN_EVENTOS", extra_eventos))
        for p in parches:
            p.start()
        try:
            return TestClient(main.app).get("/tv-live/stream")
        finally:
            for p in reversed(parches):
                p.stop()

    def test_una_sola_opcion_con_el_stream_del_partido(self):
        resp = self._get_stream(CITY)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["opciones"]), 1)
        self.assertEqual(data["opciones"][0]["label"], "")
        self.assertIn(main._free_espn_stream_de(CITY["name"]).replace(":", "%3A").replace("/", "%2F"),
                      data["opciones"][0]["url"])
        self.assertGreater(data["tiempo_restante_seg"], 0)

    def test_proxy_lleva_el_referer_que_exige_la_fuente(self):
        self.assertEqual(
            main._free_espn_opcion("", "https://barinaslive1.boldmss.com/ESPN_3/ESPN_3.isml/ESPN_3.m3u8")["referer"],
            "https://barinaslive1.boldmss.com/",
        )
        self.assertEqual(
            main._free_espn_opcion("", "http://38.252.215.33:8000/play/a00l/index.m3u8")["referer"],
            "",
        )

    def test_dos_opciones_cuando_el_evento_tiene_dos_senales(self):
        eventos = [{
            "match": ("bayern", "dortmund"),
            "league": "ger.1",
            "opciones": [
                {"label": "(ESPN Colombia)", "url": "http://senal/espn.m3u8"},
                {"label": "(Portugués)", "url": "http://senal/pt.m3u8"},
            ],
        }]
        data = self._get_stream(BAYERN, extra_eventos=eventos).json()
        self.assertEqual(len(data["opciones"]), 2)
        self.assertEqual(data["opciones"][0]["label"], "(ESPN Colombia)")
        self.assertEqual(data["opciones"][1]["label"], "(Portugués)")
        self.assertIn("http%3A%2F%2Fsenal%2Fespn.m3u8", data["opciones"][0]["url"])
        self.assertIn("http%3A%2F%2Fsenal%2Fpt.m3u8", data["opciones"][1]["url"])

    def test_senal_dash_se_entrega_sin_pasar_por_el_proxy_hls(self):
        # El proxy /hls-proxy solo reescribe m3u8: un .mpd debe salir tal cual
        # (con "dash": True) para que sus segmentos resuelvan contra su base.
        eventos = [{
            "sport": "nba",
            "match": ("nuggets", "rockets"),
            "opciones": [{"label": "STREAM 1", "url": MPD, "embed": False}],
        }]
        data = self._get_stream(NUGGETS, extra_eventos=eventos).json()
        self.assertEqual(len(data["opciones"]), 1)
        opcion = data["opciones"][0]
        self.assertEqual(opcion["label"], "STREAM 1")
        self.assertEqual(opcion["url"], MPD)
        self.assertTrue(opcion.get("dash"))
        self.assertNotIn("/hls-proxy", opcion["url"])

    def test_senal_directa_se_entrega_sin_pasar_por_el_proxy(self):
        # La PRINCIPAL del 76ers lleva ?ip= del cliente: proxied da 403, asi que
        # el backend entrega la URL tal cual y el navegador la pide desde su IP.
        eventos = [{
            "sport": "nba",
            "match": ("celtics", "76ers"),
            "opciones": [{"label": "PRINCIPAL", "url": KHALA, "embed": False, "directo": True}],
        }]
        data = self._get_stream(SIXERS, extra_eventos=eventos).json()
        self.assertEqual(len(data["opciones"]), 1)
        opcion = data["opciones"][0]
        self.assertEqual(opcion["url"], KHALA)
        self.assertTrue(opcion.get("directo"))
        self.assertNotIn("/hls-proxy", opcion["url"])

    def test_senal_dash_con_clearkey_entrega_la_llave(self):
        eventos = [{
            "sport": "nba",
            "match": ("celtics", "76ers"),
            "opciones": [{"label": "NBA DASH", "url": MPD, "embed": False,
                          "clearkey": CLAVE_76}],
        }]
        opcion = self._get_stream(SIXERS, extra_eventos=eventos).json()["opciones"][0]
        self.assertTrue(opcion.get("dash"))
        self.assertEqual(opcion.get("drm"), {
            "type": "clearkey",
            "kid": "36a385124dcdfb2f0969935065e03afa",
            "key": "ccecd65514dff22eae493b975ccf9a9e",
        })
        self.assertNotIn("/hls-proxy", opcion["url"])


class TestMatchEnVivo(unittest.TestCase):
    """/tv-live/match entrega cuartos y boxscore SOLO con el partido iniciado."""

    GAME = {
        "name": "Boston Celtics at Philadelphia 76ers",
        "date": "2026-11-11T01:00Z",
        "state": "pre",
        "id": "401909961",
        "home": {"name": "Philadelphia 76ers", "logo": "http://logo/phi.png", "score": None},
        "away": {"name": "Boston Celtics", "logo": "http://logo/bos.png", "score": None},
    }

    def _get_match(self, estado, con_caja=True):
        from fastapi.testclient import TestClient

        import sports

        ahora = datetime.now(timezone.utc)
        ventana = (ahora - timedelta(minutes=5), ahora + timedelta(minutes=60))
        cuartos = [{"value": 28, "displayValue": "28"}] * 4 if estado != "pre" else []
        detail = {
            "state": estado,
            "status": "Q3 6:34" if estado == "in" else "",
            "clock": "6:34",
            "period": 3,
            "league": "NBA",
            "teams": [
                {"id": "20", "name": "Philadelphia 76ers", "short_name": "76ers", "abbr": "PHI",
                 "homeAway": "home", "color": "006bb6", "score": 88,
                 "statistics": [], "linescores": cuartos},
                {"id": "2", "name": "Boston Celtics", "short_name": "Celtics", "abbr": "BOS",
                 "homeAway": "away", "color": "007a33", "score": 90,
                 "statistics": [], "linescores": cuartos},
            ],
            "players_box": ([{"equipo": "Philadelphia 76ers", "equipo_id": "20",
                              "jugadores": [{"id": "1", "nombre": "Tyrese Maxey", "corto": "T. Maxey",
                                             "pts": 24, "reb": 3, "ast": 6}]},
                             {"equipo": "Boston Celtics", "equipo_id": "2",
                              "jugadores": [{"id": "2", "nombre": "Jayson Tatum", "corto": "J. Tatum",
                                             "pts": 30, "reb": 8, "ast": 4}]}]
                            if con_caja else []),
        }
        parches = [
            mock.patch.object(main, "_free_espn_buscar_partido", return_value=dict(self.GAME, state=estado)),
            mock.patch.object(main, "_free_espn_ventana", return_value=ventana),
            mock.patch.object(main, "get_pagadito_plans", return_value=[]),
            mock.patch.object(sports, "get_game_detail", return_value=detail),
        ]
        for p in parches:
            p.start()
        try:
            return TestClient(main.app).get("/tv-live/match")
        finally:
            for p in reversed(parches):
                p.stop()

    def test_antes_del_partido_no_hay_boxscore_ni_cuartos(self):
        data = self._get_match("pre").json()
        self.assertEqual(data["boxscore"], [])
        self.assertEqual(data["teams"][0]["linescores"], [])
        self.assertEqual(data["estado"], "pre")

    def test_en_vivo_manda_boxscore_y_cuartos(self):
        data = self._get_match("in").json()
        self.assertEqual(len(data["boxscore"]), 2)
        self.assertEqual(data["boxscore"][0]["jugadores"][0]["corto"], "T. Maxey")
        self.assertEqual(len(data["teams"][0]["linescores"]), 4)
        self.assertEqual(data["teams"][0]["homeAway"], "home")
        self.assertEqual(data["teams"][0]["id"], "20")
        self.assertEqual(data["periodo"], 3)

    def test_cuartos_de_baloncesto_vienen_como_strings(self):
        """ESPN da los cuartos de NBA como ["25","21",...]: se normalizan."""
        crudos = ["25", "21", "22", "28"]
        self.assertEqual(main._free_espn_cuartos(crudos), [
            {"value": "25", "displayValue": "25"},
            {"value": "21", "displayValue": "21"},
            {"value": "22", "displayValue": "22"},
            {"value": "28", "displayValue": "28"},
        ])
        # Futbol/tenis ya vienen como objetos: se respetan (sin displayValue -> "-")
        self.assertEqual(
            main._free_espn_cuartos([{"value": 1, "displayValue": "1"}, {"value": None}]),
            [{"value": 1, "displayValue": "1"}, {"value": None, "displayValue": "-"}],
        )
        self.assertEqual(main._free_espn_cuartos(None), [])

    def test_la_pagina_no_recibe_caja_si_espn_no_la_publico(self):
        data = self._get_match("in", con_caja=False).json()
        self.assertEqual(data["boxscore"], [])
        self.assertEqual(len(data["teams"][0]["linescores"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
