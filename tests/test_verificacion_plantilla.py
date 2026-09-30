"""
Pruebas de la verificacion de plantilla y competicion.

Contexto: la IA se inventaba que "Tyler Mahle" jugaba en "Texas Rangers"
cuando su equipo real es Atlanta Braves. Estas pruebas fijan ese caso y
cubren los otros deportes y los casos limite del parser de entidades.

Nota: las pruebas que pegan a la red se saltan si no hay internet.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai.verificacion as v  # noqa: E402


def _red_disponible():
    try:
        return v._get(f"{v.MLB_BASE}/teams", {"sportId": 1}) is not None
    except Exception:
        return False


class TestParserEntidades(unittest.TestCase):
    """El parser NO toca la red: solo extrae nombres del texto."""

    def test_equipo_con_jugador_entre_parentesis(self):
        jugadores, equipos = v.extraer_entidades(
            "Texas Rangers (Tyler Mahle) vs Philadelphia Phillies"
        )
        self.assertEqual(jugadores, ["Tyler Mahle"])
        self.assertEqual(equipos, ["Texas Rangers", "Philadelphia Phillies"])

    def test_ambos_lados_con_jugadores(self):
        jugadores, equipos = v.extraer_entidades(
            "Lakers (LeBron James) vs Celtics (Jayson Tatum)"
        )
        self.assertIn("LeBron James", jugadores)
        self.assertIn("Jayson Tatum", jugadores)
        self.assertEqual(equipos, ["Lakers", "Celtics"])

    def test_equipos_de_una_sola_palabra(self):
        # "Barcelona" / "Inter" / "Celtics" son validos con una palabra.
        jugadores, equipos = v.extraer_entidades("Real Madrid vs Barcelona")
        self.assertEqual(equipos, ["Real Madrid", "Barcelona"])
        self.assertEqual(jugadores, [])

    def test_vocabulario_no_es_jugador(self):
        # Palabras del analisis que empiezan en mayuscula no son jugadores.
        jugadores, _ = v.extraer_entidades("Partido de Beisbol Over Under Handicap")
        self.assertEqual(jugadores, [])

    def test_mensajes_de_charla_no_producen_contexto(self):
        for texto in ("hola", "..", "123", "dame el pick del partido de hoy"):
            self.assertEqual(v.construir_contexto_verificacion(texto), "")


class TestVerificacionOnline(unittest.TestCase):
    """Requieren internet. Se saltan solas si la red no responde."""

    @classmethod
    def setUpClass(cls):
        if not _red_disponible():
            raise unittest.SkipTest("Sin acceso a las APIs de MLB")

    def test_mahle_no_juega_en_rangers(self):
        """El bug reportado: Mahle NUNCA debe validar contra Texas."""
        r = v.verificar_jugador_en_equipo("Tyler Mahle", "Texas Rangers")
        self.assertTrue(r["encontrado"])
        self.assertFalse(r["pertenece"])
        self.assertEqual(r["equipo_real"], "Atlanta Braves")
        self.assertIn("NO en Texas Rangers", r["motivo"])

    def test_judge_si_juega_en_yankees(self):
        """Control: un jugador que SI esta en su equipo debe validar bien."""
        r = v.verificar_jugador_en_equipo("Aaron Judge", "Yankees")
        self.assertTrue(r["encontrado"])
        self.assertTrue(r["pertenece"], f"Aaron Judge deberia validar: {r['motivo']}")
        self.assertEqual(r["motivo"], "")

    def test_partido_real_de_la_fecha(self):
        """El calendario oficial manda sobre lo que diga el mensaje."""
        ctx = v.contexto_equipo("Philadelphia Phillies", "2026-09-30")
        self.assertTrue(ctx["encontrado"])
        self.assertEqual(ctx["competicion"].split()[0], "MLB")
        self.assertEqual(ctx["rival_real"], "Atlanta Braves")

    def test_plantilla_contiene_a_los_titulares(self):
        ctx = v.contexto_equipo("Philadelphia Phillies", "2026-09-30")
        self.assertTrue(ctx["plantilla"])
        self.assertIn("J.T. Realmuto", ctx["plantilla"])

    def test_alerta_de_plantilla_en_el_contexto(self):
        """El texto inyectado debe avisar del error de roster."""
        texto = v.construir_contexto_verificacion(
            "Texas Rangers (Tyler Mahle) vs Philadelphia Phillies", "2026-09-30"
        )
        self.assertIn("ALERTA DE PLANTILLA", texto)
        self.assertIn("Atlanta Braves", texto)
        self.assertIn("Philadelphia Phillies vs Atlanta Braves", texto)

    def test_equipo_de_futbol_identifica_competicion(self):
        ctx = v.contexto_equipo("Barcelona")
        self.assertTrue(ctx["encontrado"], "ESPN deberia resolver Barcelona")
        self.assertEqual(ctx["liga"], "esp.1")
        self.assertEqual(ctx["deporte"], "futbol")


class TestInvestigacionProfunda(unittest.TestCase):
    """El bloque que se inyecta antes de pedirle el analisis a la IA."""

    @classmethod
    def setUpClass(cls):
        if not _red_disponible():
            raise unittest.SkipTest("Sin acceso a las APIs de MLB")

    def test_incluye_motivacion_y_contexto(self):
        txt = v.construir_investigacion(
            "Philadelphia Phillies vs Atlanta Braves", "2026-09-30",
            con_noticias=False,
        )
        self.assertIn("INVESTIGACION PROFUNDA", txt)
        self.assertIn("MOTIVACION:", txt)
        self.assertIn("Tabla:", txt)
        self.assertIn("H2H temporada:", txt)
        self.assertIn("Carga de calendario:", txt)
        # La competicion debe decir MLB, no "National League" a secas.
        self.assertIn("MLB", txt)

    def test_h2h_solo_partidos_disputados(self):
        """Los partidos programados vienen con marcador 0-0 y deben excluirse."""
        h2h = v.mlb_h2h_equipo(143, 144, 2026)
        self.assertTrue(h2h)
        jugados = [j for j in h2h if j["marcador"] not in ("0-0", "None-None")]
        self.assertTrue(jugados, "Deberia haber H2H ya disputado")
        for j in jugados:
            self.assertNotEqual(j["marcador"], "0-0")

    def test_motivacion_texto_segun_situacion(self):
        """La motivacion cambia segun donde este el equipo."""
        elimination = v.motivation_text({
            "posicion_real": 14, "total_equipos": 15, "eliminacion": "3",
            "gamesBack": "20", "streak": "L2",
            "lider": {"nombre": "Yankees", "wins": 100, "losses": 50},
        })
        self.assertIn("3 juegos de la eliminacion", elimination)
        self.assertIn("vida o muerte", elimination)
        self.assertIn("L2", elimination)

        campeon = v.motivation_text({
            "posicion_real": 1, "total_equipos": 15, "divisionChamp": True,
            "clinched": True, "gamesBack": "-", "eliminacion": "-",
            "streak": "W3",
            "lider": {"nombre": "Yankees", "wins": 100, "losses": 50},
        })
        self.assertIn("GANO su division", campeon)
        self.assertNotIn("eliminacion", campeon)

    def test_carga_de_calendario_es_realista(self):
        """Un equipo no juega 81 partidos en 7 dias (bug del filtro de fechas)."""
        carga = v.mlb_carga_calendario(143, 2026)
        self.assertIsNotNone(carga["jugados_7d"])
        self.assertLessEqual(carga["jugados_7d"], 10)
        self.assertLessEqual(carga["proximos_7d"], 10)

    def test_charla_no_dispara_investigacion(self):
        for texto in ("hola", "..", "123", "gracias!"):
            self.assertEqual(v.construir_investigacion(texto), "")

    def test_alerta_roster_sigue_presente(self):
        """La correccion de plantilla no se perdio al anadir la investigacion."""
        txt = v.construir_investigacion(
            "Texas Rangers (Tyler Mahle) vs Philadelphia Phillies", "2026-09-30",
            con_noticias=False,
        )
        self.assertIn("Atlanta Braves", txt)
        self.assertIn("Philadelphia Phillies vs Atlanta Braves", txt)


if __name__ == "__main__":
    # unittest escribe el progreso en stderr; se usa un TextTestRunner con
    # stream explicito para que no se confunda con un error de powershell.
    import io
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=2)
    resultado = runner.run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(f"\npruebas: {resultado.testsRun}  fallos: {len(resultado.failures)}  "
          f"errores: {len(resultado.errors)}")
    for caso, traza in resultado.failures + resultado.errors:
        print(f"\n--- {caso} ---\n{traza}")
    sys.exit(0 if resultado.wasSuccessful() else 1)
