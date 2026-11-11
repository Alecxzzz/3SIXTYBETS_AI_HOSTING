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
    def _run(self, detalle, anular=True, picks=None, parsed=("double_chance", "1x", None),
             purgar=False):
        # purgar=False por defecto: estos tests fijan la invariante de "nunca
        # ANULADO", no la purga. La fixture es de hace meses, asi que con la
        # purga activa el pick se borraria y 'sin_datos' seria 0.
        fake_db = FakeDB(picks if picks is not None else [pick()])
        with mock.patch.object(router, "buscar_completo", return_value=detalle), \
                mock.patch("db.run_query", side_effect=fake_db), \
                mock.patch("data_engine.pick_parser.parsear_pick", return_value=parsed):
            res = router.resolve_picks(
                limite=10,
                solo_hoy=False,
                anular_no_resolubles=anular,
                purgar_vencidos=purgar,
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


class PurgaVencidosTest(unittest.TestCase):
    """Los picks que no se pueden verificar se BORRAN pasadas 72h.

    Antes se quedaban PENDIENTE para siempre y el panel mostraba siempre
    "0 resueltos / N sin datos" (medido: 19 de 32 picks eran basura eterna).
    """

    def _run(self, pick_dict, detalle, parsed=None, purgar=True):
        fake_db = FakeDB([pick_dict])
        with mock.patch.object(router, "buscar_completo", return_value=detalle), \
                mock.patch("db.run_query", side_effect=fake_db), \
                mock.patch("data_engine.pick_parser.parsear_pick", return_value=parsed):
            res = router.resolve_picks(limite=10, solo_hoy=False, anular_no_resolubles=False,
                                       purgar_vencidos=purgar, admin={"role": "admin"})
        return res, fake_db.updates

    def test_pick_viejo_no_resoluble_se_purga(self):
        viejo = pick(pid="viejo")  # event_date 2026-10-21: mas de 72h
        res, updates = self._run(viejo, {"found": False})

        self.assertEqual(res["purgados"], 1)
        self.assertEqual(res["sin_datos"], 0)
        self.assertTrue(any("delete" in sql.lower() for sql, _ in updates))

    def test_pick_reciente_se_espera(self):
        from datetime import datetime, timedelta, timezone

        reciente = dict(pick(pid="nuevo"))
        reciente["event_date"] = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime(
            "%Y-%m-%dT%H:%MZ")
        res, updates = self._run(reciente, {"found": False})

        self.assertEqual(res["purgados"], 0)
        self.assertEqual(res["sin_datos"], 1)
        self.assertEqual(updates, [])

    def test_mercado_prohibido_se_cuenta_y_luego_se_purga(self):
        """'primer juego' no se intenta: se informa (omitidos) y si es viejo se borra."""
        prohibido = pick(pid="prohibido", market="Primer juego con servicio - Ganador",
                         selection="X")
        res, _ = self._run(prohibido, {"found": False}, purgar=False)
        self.assertEqual(res["omitidos"], 1)
        self.assertEqual(res["sin_datos"], 0)

        res, updates = self._run(prohibido, {"found": False}, purgar=True)
        self.assertEqual(res["purgados"], 1)
        self.assertEqual(res["omitidos"], 0)
        self.assertTrue(any("delete" in sql.lower() for sql, _ in updates))

    def test_suma_cuadra(self):
        """revisados = resueltos + en_juego + sin_datos + omitidos + purgados."""
        picks = [
            pick(pid="a", market="Primer juego con servicio - Ganador", selection="X"),
            pick(pid="b"),
            dict(pick(pid="c")),
        ]
        picks[2]["event_date"] = "2026-11-11T03:00Z"
        fake_db = FakeDB(picks)

        def buscar(sport, home, away, fecha, liga):
            return {"found": True, "estado": "in", "home_score": 1, "away_score": 0}

        with mock.patch.object(router, "buscar_completo", side_effect=buscar), \
                mock.patch("db.run_query", side_effect=fake_db), \
                mock.patch("data_engine.pick_parser.parsear_pick", return_value=None):
            res = router.resolve_picks(limite=10, solo_hoy=False, anular_no_resolubles=False,
                                       purgar_vencidos=True, admin={"role": "admin"})

        suma = (res["resueltos"] + res["en_juego"] + res["sin_datos"]
                + res["omitidos"] + res["purgados"] + res["anulados"])
        self.assertEqual(suma, res["total_pendientes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
