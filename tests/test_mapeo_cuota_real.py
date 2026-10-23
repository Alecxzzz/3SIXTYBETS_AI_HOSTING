"""
Pruebas del mapeo pick -> precio (cuota real que se publica en el pick).

Contexto: `_precio_en_mercados` traduce el mercado/seleccion de un pronostico
(que viene en texto libre del catalogo) a los mercados de odds-api.io para
leer el precio de Bet365. Ese mapeo tenia tres fallos que asignaban al pick
la cuota de OTRO mercado:

- Ignoraba la mitad del partido: un pick de 2ª mitad se comparaba con el
  mercado de partido completo ("Double Chance" en vez de "Double Chance 2H").
- "Hándicap 1x2" entraba en la rama 1X2 y devolvia el precio del ML.
- Las claves se buscaban con acentos ("hándicap", "más de"), asi que esas
  ramas nunca se ejecutaban.

Las cuotas de abajo son las REALES que devolvio Bet365 para
"Deportivo Alaves vs Malaga CF" (odds-api.io, evento 72478646), recortadas a
los mercados del catalogo. Ninguna prueba toca la red.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as d  # noqa: E402

BET365 = [
    {"name": "ML", "odds": [{"home": "1.650", "draw": "3.600", "away": "5.750"}]},
    {"name": "Draw No Bet", "odds": [{"home": "1.222", "away": "4.000"}]},
    {"name": "Double Chance", "odds": [{"1X": "1.142", "12": "1.285", "X2": "2.200"}]},
    {"name": "Spread", "odds": [{"hdp": -2.5, "home": "5.900", "away": "1.130"},
                                {"hdp": -0.75, "home": "1.875", "away": "1.975"}]},
    {"name": "Goals Over/Under", "odds": [{"hdp": 2.5, "over": "2.000", "under": "1.800"}]},
    {"name": "Both Teams To Score", "odds": [{"yes": "1.950", "no": "1.800"}]},
    {"name": "ML HT", "odds": [{"home": "2.250", "draw": "2.200", "away": "5.500"}]},
    {"name": "Totals HT", "odds": [{"hdp": 0.5, "over": "1.425", "under": "2.750"}]},
    {"name": "European Handicap",
     "odds": [{"hdp": -3, "home": "15.000", "draw": "8.500", "away": "1.125"},
              {"hdp": -2, "home": "6.000", "draw": "4.750", "away": "1.400"}]},
    {"name": "European Handicap HT",
     "odds": [{"hdp": -1, "home": "6.500", "draw": "3.100", "away": "1.615"}]},
    {"name": "ML 2H", "odds": [{"home": "2.000", "draw": "2.400", "away": "5.000"}]},
    {"name": "Both Teams To Score 2H", "odds": [{"yes": "3.750", "no": "1.250"}]},
    {"name": "Double Chance HT", "odds": [{"1X": "1.125", "12": "1.615", "X2": "1.571"}]},
]

# El pick es "Malaga vs Alaves" y odds-api tiene el duelo invertido.
HOME, AWAY = "Malaga", "Alaves"
LOCAL_EN_ODDS = False


def _precio(market, selection):
    return d._precio_en_mercados(
        BET365, "soccer", HOME, AWAY, market, selection, "", LOCAL_EN_ODDS
    )


class TestMapeoMercado(unittest.TestCase):
    """El precio devuelto tiene que ser del MISMO mercado que el pick."""

    def test_doble_oportunidad_con_nombres(self):
        # "A o B" = 12. Antes devolvia None: el pick se quedaba sin comparar.
        self.assertAlmostEqual(
            _precio("Doble oportunidad", "Deportivo Alaves o  Malaga"), 1.285
        )

    def test_doble_oportunidad_segunda_mitad_sin_datos(self):
        # Bet365 no publica "Double Chance 2H": no se inventa ni se usa el de
        # partido completo (1.285 seria otro mercado).
        self.assertIsNone(
            _precio("2ª Mitad - doble oportunidad", "Deportivo Alaves o  Malaga")
        )

    def test_doble_oportunidad_primera_mitad(self):
        self.assertAlmostEqual(
            _precio("1ª Mitad - doble oportunidad", "Deportivo Alaves o Malaga"), 1.615
        )

    def test_ganador_y_mitades(self):
        self.assertAlmostEqual(_precio("Ganador", "Malaga"), 5.75)
        self.assertAlmostEqual(_precio("1ª Mitad - ganador", "Malaga"), 5.5)
        self.assertAlmostEqual(_precio("2ª Mitad - ganador", "Malaga"), 5.0)

    def test_handicap_1x2_no_devuelve_el_ml(self):
        # Antes "1x2" hacia entrar el pick en la rama del ML (5 camino):
        # el usuario veia la cuota del ganador como si fuera su handicap.
        self.assertAlmostEqual(_precio("Hándicap 1x2", "Malaga (2:0)"), 1.4)
        self.assertAlmostEqual(_precio("1ª Mitad - hándicap 1X2", "Malaga (1:0)"), 1.615)

    def test_handicap_asiatico(self):
        self.assertAlmostEqual(_precio("Hándicap asiático", "Malaga +2.5"), 1.13)

    def test_over_under_con_acentos(self):
        self.assertAlmostEqual(_precio("Más de 2.5 goles", "Más de 2.5"), 2.0)
        self.assertAlmostEqual(_precio("1ª Mitad - más de 0.5 goles", "Más de 0.5"), 1.425)

    def test_ambos_equipos_marcan(self):
        self.assertAlmostEqual(_precio("Ambos equipos marcan", "Sí"), 1.95)
        self.assertAlmostEqual(_precio("2ª Mitad - ambos equipos marcan", "Sí"), 3.75)

    def test_sin_empate(self):
        self.assertAlmostEqual(_precio("Sin empate", "Malaga"), 4.0)

    def test_mercado_desconocido_no_devuelve_nada(self):
        self.assertIsNone(_precio("Mercado que no existe", "Algo raro"))
        self.assertIsNone(_precio("Ganador", "Equipo de otro partido"))


if __name__ == "__main__":
    unittest.main()
