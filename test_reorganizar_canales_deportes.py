# -*- coding: utf-8 -*-
"""
Test de la reorganizacion de canales de deportes
(reorganizar_canales_deportes.py).

Se protege el resultado pedido:
- Claro Sports 1 HD queda con a0ey como opcion 2 y "Claro Sports 1 SD" se borra.
- DSPORTS 1 se crea con opcion 1 = a00q y opcion 2 = a0s8; "DIRECTV SPORTS" se borra.
- TUDN pasa a opcion 1 = a07z (antes TUDN-IXP, que se borra) y opcion 2 = ca039.
- FOX SPORT -> "FOX DEPORTES (ESPAÑOL)" (a080) y FOX SPORT 2-IXP ->
  "FOX DEPORTES 2 ESPAÑOL" (a081).
- Los SPOR TV 1 BR / SPOR TV 3 BR / SPORTV 2 BR no se tocan.
- Sin --apply no se escribe nada en la BD.
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import reorganizar_canales_deportes as reorg


BD_INICIAL = [
    {"id": "c1", "name": "Claro Sports 1 HD", "stream": "http://138.59.177.78:9999/play/a0bk/index.m3u8",
     "alternatives": None},
    {"id": "c2", "name": "Claro Sports 1 SD", "stream": reorg.A0EY, "alternatives": None},
    {"id": "c3", "name": "DIRECTV SPORTS", "stream": reorg.A0S8, "alternatives": json.dumps([reorg.A0S8])},
    {"id": "c4", "name": "FOX SPORT", "stream": "http://138.59.177.78:9999/play/a080/index.m3u8",
     "alternatives": None},
    {"id": "c5", "name": "FOX SPORT 2-IXP", "stream": "http://138.59.177.78:9999/play/a081/index.m3u8",
     "alternatives": None},
    {"id": "c6", "name": "SPOR TV 1 BR", "stream": "http://fonte.lista.luxe/live/iptvgood/101011/3811.m3u8",
     "alternatives": None},
    {"id": "c7", "name": "SPOR TV 3 BR", "stream": "http://fente.lista.luxe/live/iptvgood/101011/3806.m3u8",
     "alternatives": None},
    {"id": "c8", "name": "SPORTV 2 BR", "stream": "http://fente.lista.luxe/live/iptvgood/101011/3809.m3u8",
     "alternatives": None},
    {"id": "c9", "name": "TUDN", "stream": reorg.CA039, "alternatives": json.dumps([reorg.A07Z])},
    {"id": "c10", "name": "TUDN-IXP", "stream": reorg.A07Z, "alternatives": None},
]


class FakeDB:
    """BD en memoria con lo minimo que usa el script (select/update/insert/delete)."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def run_query(self, query, params=None, fetchone=False):
        q = " ".join(query.split()).upper()
        if q.startswith("SELECT"):
            return [dict(r) for r in self.rows]
        if q.startswith("UPDATE"):
            cols = re.findall(r"(\w+) = %s", query)
            cid = params[-1]
            for row in self.rows:
                if row["id"] == cid:
                    for col, value in zip(cols, params[:-1]):
                        row[col] = value
                    return True
            return False
        if q.startswith("INSERT"):
            cid, name, stream, alts, created, updated = params
            self.rows.append({"id": cid, "name": name, "stream": stream, "alternatives": alts})
            return True
        return True

    def delete_channel(self, channel_id):
        antes = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != channel_id]
        return len(self.rows) < antes

    def by_name(self, name):
        for row in self.rows:
            if row["name"] == name:
                return row
        return None


class ReorganizarCanalesTest(unittest.TestCase):
    def _correr(self, argv):
        fake = FakeDB(BD_INICIAL)
        respaldo = os.path.join(tempfile.mkdtemp(prefix="reorg_"), reorg.RESPALDO)
        with mock.patch.object(reorg, "RESPALDO", respaldo), \
                mock.patch.object(reorg.db, "run_query", side_effect=fake.run_query), \
                mock.patch.object(reorg.db, "delete_channel", side_effect=fake.delete_channel), \
                mock.patch.object(reorg.db, "secrets", mock.MagicMock()), \
                mock.patch.object(reorg.sys, "argv", ["reorganizar_canales_deportes.py"] + argv), \
                contextlib.redirect_stdout(io.StringIO()):
            reorg.db.secrets.token_urlsafe.return_value = "nuevo-id"
            codigo = reorg.main()
        return codigo, fake, respaldo

    def test_dry_run_no_toca_la_bd(self):
        codigo, fake, respaldo = self._correr([])

        self.assertEqual(codigo, 0)
        self.assertEqual([r["name"] for r in fake.rows], [r["name"] for r in BD_INICIAL])
        self.assertFalse(os.path.exists(respaldo))

    def test_apply_deja_la_bd_como_se_pide(self):
        codigo, fake, respaldo = self._correr(["--apply"])

        self.assertEqual(codigo, 0)
        self.assertTrue(os.path.exists(respaldo))
        self.assertEqual(
            sorted(r["name"] for r in fake.rows),
            sorted([
                "Claro Sports 1 HD", "DSPORTS 1", "FOX DEPORTES (ESPAÑOL)",
                "FOX DEPORTES 2 ESPAÑOL", "SPOR TV 1 BR", "SPOR TV 3 BR",
                "SPORTV 2 BR", "TUDN",
            ]),
        )
        self.assertEqual(json.loads(fake.by_name("Claro Sports 1 HD")["alternatives"]), [reorg.A0EY])
        self.assertEqual(fake.by_name("DSPORTS 1")["stream"], reorg.A00Q)
        self.assertEqual(json.loads(fake.by_name("DSPORTS 1")["alternatives"]), [reorg.A0S8])
        self.assertEqual(fake.by_name("TUDN")["stream"], reorg.A07Z)
        self.assertEqual(json.loads(fake.by_name("TUDN")["alternatives"]), [reorg.CA039])
        self.assertEqual(fake.by_name("FOX DEPORTES (ESPAÑOL)")["stream"],
                         "http://138.59.177.78:9999/play/a080/index.m3u8")
        self.assertEqual(fake.by_name("FOX DEPORTES 2 ESPAÑOL")["stream"],
                         "http://138.59.177.78:9999/play/a081/index.m3u8")
        for borrado in ("Claro Sports 1 SD", "TUDN-IXP", "DIRECTV SPORTS"):
            self.assertIsNone(fake.by_name(borrado))
        self.assertEqual(fake.by_name("SPOR TV 1 BR")["stream"],
                         "http://fonte.lista.luxe/live/iptvgood/101011/3811.m3u8")

    def test_segunda_pasada_no_duplica_ni_falla(self):
        _, fake, _ = self._correr(["--apply"])
        codigo, fake2, _ = self._correr(["--apply"])

        self.assertEqual(codigo, 0)
        self.assertEqual(len(fake2.rows), len(fake.rows))
        self.assertEqual(len([r for r in fake2.rows if r["name"] == "DSPORTS 1"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
