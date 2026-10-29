# -*- coding: utf-8 -*-
"""
Test del mantenimiento "borrar todos los canales ESPN" (eliminar_canales_espn_todos.py).

Lo que se protege:
1. El filtro es por NOMBRE con LIKE '%ESPN%' (case-insensitive) y NUNCA por la
   URL del stream: un canal que no se llame ESPN no se borra aunque su stream
   apunte a un host/ path con "ESPN" (barinaslive1.boldmss.com/ESPN/...).
2. Dry-run (sin --apply) no borra NADA.
3. Con --apply se borra exactamente lo que devolvio el SELECT y se respalda
   antes en respaldo_canales_espn.json.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import eliminar_canales_espn_todos as limpiar


ESPN = [
    {"id": "e1", "name": "ESPN", "stream": "http://190.61.47.54:8000/play/a004/index.m3u8"},
    {"id": "e2", "name": "ESPN 1 IXP", "stream": "http://138.59.177.78:9999/play/a003/index.m3u8"},
    {"id": "e3", "name": "ESPN2 HD", "stream": "http://181.224.255.210:8001/play/a0oj/index.m3u8"},
    {"id": "e4", "name": "ESPN 5 Sur (ESPN07)", "stream": "http://190.61.47.54:8000/play/a00a/index.m3u8"},
    {"id": "e5", "name": "6 ESPN 4K_a0a0", "stream": "http://138.59.177.78:9999/play/a0n6/index.m3u8"},
]


def _fake_run_query(filas):
    """SELECT * -> filas; SELECT count(*) -> {"n": N}; DELETE -> True."""
    def runner(query, params=None, fetchone=False):
        if query.strip().upper().startswith("SELECT"):
            if "COUNT(*)" in query.upper():
                return {"n": 1}
            return [dict(f) for f in filas]
        return True
    return runner


class EliminarCanalesEspnTest(unittest.TestCase):
    def _run(self, filas, argv):
        """Ejecuta main() con la BD mockeada; devuelve (codigo, borrados, respaldo_path)."""
        tmp = tempfile.mkdtemp(prefix="espn_")
        respaldo = os.path.join(tmp, limpiar.RESPALDO)

        with mock.patch.object(limpiar, "RESPALDO", respaldo), \
                mock.patch.object(limpiar.db, "run_query",
                                  side_effect=_fake_run_query(filas)), \
                mock.patch.object(limpiar.db, "delete_channel", return_value=True) as borrar, \
                mock.patch.object(limpiar.sys, "argv", ["eliminar_canales_espn_todos.py"] + argv):
            codigo = limpiar.main()
        return codigo, borrar, respaldo

    def test_filtra_por_nombre_y_no_por_url(self):
        """El SELECT usa el patron %ESPN% sobre el nombre."""
        with mock.patch.object(limpiar.db, "run_query", return_value=[]) as query:
            limpiar.main()
        sql, params = query.call_args.args[0], query.call_args.args[1]
        self.assertIn("upper(name) like", sql.lower())
        self.assertEqual(params, ("%ESPN%",))

    def test_dry_run_no_borra_nada(self):
        codigo, borrar, respaldo = self._run(ESPN, [])

        self.assertEqual(codigo, 0)
        borrar.assert_not_called()
        self.assertFalse(os.path.exists(respaldo))

    def test_apply_borra_solo_los_canales_espn(self):
        codigo, borrar, respaldo = self._run(ESPN, ["--apply"])

        self.assertEqual(codigo, 0)
        self.assertEqual(
            sorted(call.args[0] for call in borrar.call_args_list),
            sorted(f["id"] for f in ESPN),
        )
        self.assertTrue(os.path.exists(respaldo))
        with open(respaldo, encoding="utf-8") as fh:
            guardado = json.load(fh)
        self.assertEqual([f["id"] for f in guardado], [f["id"] for f in ESPN])

    def test_sin_canales_espn_no_toca_la_bd(self):
        codigo, borrar, _ = self._run([], ["--apply"])

        self.assertEqual(codigo, 0)
        borrar.assert_not_called()

    def test_falla_si_un_borrado_no_se_pudo_hacer(self):
        """Un DELETE fallido no debe reportarse como exito."""
        with mock.patch.object(limpiar, "RESPALDO", os.path.join(tempfile.mkdtemp(), limpiar.RESPALDO)), \
                mock.patch.object(limpiar.db, "run_query",
                                  side_effect=_fake_run_query(ESPN)), \
                mock.patch.object(limpiar.db, "delete_channel", return_value=False), \
                mock.patch.object(limpiar.sys, "argv", ["x", "--apply"]):
            codigo = limpiar.main()

        self.assertEqual(codigo, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
