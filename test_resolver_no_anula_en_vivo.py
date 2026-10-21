# -*- coding: utf-8 -*-
"""
Test de regresion del boton "Verificar y resolver resultados" (POST /resolve/picks).

Bug real en produccion: se generaban los picks de Champions y, al pulsar
"Verificar y resolver resultados" (que mandaba anular_no_resolubles=true), TODOS
los picks de ese dia desaparecian del dashboard.

Causa: el endpoint filtraba solo por `event_date < ahora`, asi que los picks de
partidos EN VIVO (el dashboard los genera a proposito con state="in") entraban
al resolver. Al no haber marcador final, resolver_mercado devolvia NO_RESOLUBLE
y con anular_no_resolubles=true se marcaban ANULADO. Como list_picks oculta los
ANULADO (y el arranque los borra con el resto de ANULADO legacy), el pick
desaparecia minutos despues de crearse.

Invariante fijada aqui: solo se toca un pick cuyo partido YA TERMINO (post).
"""
import sys
import unittest
from unittest import mock

# OJO: `data_engine.router` como atributo del paquete es el APIRouter exportado
# en data_engine/__init__.py, no el modulo. El modulo real esta en sys.modules.
import data_engine.router  # noqa: F401  (registra el modulo)

router = sys.modules["data_engine.router"]


def pick(pid="p1", market="Doble oportunidad", selection="1X"):
    return {
        "id": pid,
        "sport": "soccer",
        "league": "uefa.champions",
        "home_name": "Feyenoord Rotterdam",
        "away_name": "Real Betis",
        "event_date": "2026-10-21T19:00Z",
        "market": market,
        "titulo": market,
        "selection": selection,
    }


class FakeDB:
    """Sustituye a db.run_query: sirve los picks y registra los UPDATE."""

    def __init__(self, picks):
        self.picks = picks
        self.updates = []

    def __call__(self, sql, params=None, **kwargs):
        sql_norm = " ".join(str(sql).split())
        if sql_norm.lower().startswith("select"):
            return self.picks
        self.updates.append((" ".join(sql_norm.split()), params))
        return True


class ResolverNoAnulaEnVivoTest(unittest.TestCase):
    def _run(self, detalle, anular=True, picks=None, parsed=("double_chance", "1x", None)):
        fake_db = FakeDB(picks if picks is not None else [pick()])
        with mock.patch.object(router, "buscar_completo", return_value=detalle), \
                mock.patch("db.run_query", side_effect=fake_db), \
                mock.patch("data_engine.pick_parser.parsear_pick", return_value=parsed):
            res = router.resolve_picks(
                limite=10,
                solo_hoy=False,
                anular_no_resolubles=anular,
                admin={"role": "admin"},
            )
        return res, fake_db.updates

    def test_partido_en_vivo_no_se_anula(self):
        """EN VIVO (state=in): ni se resuelve ni se anula, aunque anular=true."""
        detalle = {
            "found": True,
            "estado": "in",
            "home_score": 1,
            "away_score": 0,
        }
        res, updates = self._run(detalle, anular=True)

        self.assertEqual(updates, [], "un pick en juego no debe tocarse")
        self.assertEqual(res["en_juego"], 1)
        self.assertEqual(res["anulados"], 0)
        self.assertEqual(res["resueltos"], 0)

    def test_partido_programado_no_se_anula(self):
        """PROGRAMADO (state=pre): tampoco se toca."""
        detalle = {"found": True, "estado": "pre", "home_score": None, "away_score": None}
        res, updates = self._run(detalle, anular=True)

        self.assertEqual(updates, [])
        self.assertEqual(res["en_juego"], 1)
        self.assertEqual(res["anulados"], 0)

    def test_sin_marcador_final_no_se_anula(self):
        """Fuente sin estado ni marcador: se espera, no se anula."""
        detalle = {"found": True, "estado": None, "home_score": None, "away_score": None}
        res, updates = self._run(detalle, anular=True)

        self.assertEqual(updates, [])
        self.assertEqual(res["en_juego"], 1)
        self.assertEqual(res["anulados"], 0)

    def test_partido_no_encontrado_no_se_anula(self):
        """Si la fuente no encuentra el partido, sigue PENDIENTE."""
        res, updates = self._run({"found": False}, anular=True)

        self.assertEqual(updates, [])
        self.assertEqual(res["sin_datos"], 1)
        self.assertEqual(res["anulados"], 0)

    def test_partido_terminado_si_resuelve(self):
        """FINALIZADO (state=post): el marcador real decide ACIERTO."""
        detalle = {"found": True, "estado": "post", "home_score": 2, "away_score": 1}
        res, updates = self._run(detalle, anular=True)

        self.assertEqual(len(updates), 1)
        self.assertIn("ACIERTO", updates[0][1])
        self.assertEqual(res["resueltos"], 1)
        self.assertEqual(res["anulados"], 0)

    def test_mercado_no_interpretable_solo_se_anula_si_termino(self):
        """Mercado raro: se anula SOLO cuando el partido ya termino."""
        en_vivo = {"found": True, "estado": "in", "home_score": 0, "away_score": 0}
        res, updates = self._run(en_vivo, anular=True, parsed=None)
        self.assertEqual(updates, [])
        self.assertEqual(res["anulados"], 0)

        terminado = {"found": True, "estado": "post", "home_score": 0, "away_score": 0}
        res, updates = self._run(terminado, anular=True, parsed=None)
        self.assertEqual(len(updates), 1)
        self.assertIn("ANULADO", updates[0][0])
        self.assertEqual(res["anulados"], 1)

    def test_anular_false_nunca_anula(self):
        """Con anular=false (lo que manda el dashboard) no hay ANULADO jamas."""
        terminado = {"found": True, "estado": "post", "home_score": 0, "away_score": 0}
        res, updates = self._run(terminado, anular=False, parsed=None)

        self.assertEqual(updates, [])
        self.assertEqual(res["anulados"], 0)
        self.assertEqual(res["sin_datos"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
