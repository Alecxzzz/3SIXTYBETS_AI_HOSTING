# -*- coding: utf-8 -*-
"""Tests de seguridad (security.py + sesiones hasheadas de db.py).

Lo importante de estos tests NO es que las funciones devuelvan algo, sino que
demuestren las tres cosas que estaban rotas:

  1. Un `X-Forwarded-For` inventado por el cliente NO puede cambiar la IP con
     la que se le limita. Era el fallo que anulaba todos los rate limits.
  2. La cuenta se bloquea aunque el atacante cambie de IP.
  3. Un token de sesion NO sirve si se lee la base de datos (solo su hash),
     y las sesiones antiguas (en claro) se migran sin echar a nadie.
"""

import unittest
from unittest import mock

from fastapi import HTTPException

import security


class _Client:
    def __init__(self, host):
        self.host = host


class _URL:
    def __init__(self, scheme="https", path="/"):
        self.scheme = scheme
        self.path = path


class _Req:
    """Peticion minima con lo que usa security.py (sin montar Starlette)."""

    def __init__(self, headers=None, client="10.0.0.9", scheme="https",
                 cookies=None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = _Client(client)
        self.url = _URL(scheme)
        self.cookies = cookies or {}


class _Resp:
    def __init__(self):
        self.headers = {}
        self.cookie = None

    def set_cookie(self, **kwargs):
        self.cookie = kwargs

    def delete_cookie(self, key, path="/"):
        self.cookie = {"deleted": key}


class TestIPReal(unittest.TestCase):
    def test_xff_falsificado_al_principio_se_ignora(self):
        """El atacante PREFIJA su valor; el proxy añade la IP real al final."""
        req = _Req(headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
        self.assertEqual(security.ip_del_cliente(req), "203.0.113.7")

    def test_xff_de_un_solo_valor(self):
        req = _Req(headers={"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(security.ip_del_cliente(req), "203.0.113.7")

    def test_sin_xff_usa_la_conexion(self):
        req = _Req(client="198.51.100.5")
        self.assertEqual(security.ip_del_cliente(req), "198.51.100.5")

    def test_xff_basura_no_contamina(self):
        req = _Req(headers={"X-Forwarded-For": "no-es-una-ip"}, client="198.51.100.5")
        self.assertEqual(security.ip_del_cliente(req), "198.51.100.5")

    def test_dos_saltos_de_proxy(self):
        """Con Cloudflare + Railway son 2 saltos: se lee el penultimo."""
        req = _Req(headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7, 10.0.0.1"})
        with mock.patch.object(security, "PROXY_HOPS", 2):
            self.assertEqual(security.ip_del_cliente(req), "203.0.113.7")

    def test_cloudflare_prioriza_su_cabecera(self):
        req = _Req(headers={"CF-Connecting-IP": "203.0.113.9",
                            "X-Forwarded-For": "1.2.3.4"})
        with mock.patch.object(security, "CONFIAR_CLOUDFLARE", True):
            self.assertEqual(security.ip_del_cliente(req), "203.0.113.9")


class TestLimites(unittest.TestCase):
    def setUp(self):
        security._bucket.clear()
        security._fallos.clear()
        security._bloqueos.clear()

    def test_bloquea_al_pasar_el_maximo(self):
        for _ in range(3):
            security.limite("prueba", 3, 60)
        with self.assertRaises(HTTPException) as ctx:
            security.limite("prueba", 3, 60)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_claves_distintas_no_se_mezclan(self):
        security.limite("a", 1, 60)
        security.limite("b", 1, 60)  # no debe lanzar

    def test_bloqueo_de_cuenta_tras_n_fallos(self):
        for _ in range(security.FALLOS_ANTES_DE_BLOQUEO):
            security.registrar_fallo("Victima")
        # Aunque el atacante cambie de IP, la CUENTA esta bloqueada.
        self.assertTrue(security.bloqueado("victima"))
        security.limpiar_fallos("victima")
        self.assertFalse(security.bloqueado("victima"))

    def test_login_correcto_limpia_los_fallos(self):
        for _ in range(security.FALLOS_ANTES_DE_BLOQUEO - 1):
            security.registrar_fallo("user")
        security.limpiar_fallos("user")
        for _ in range(security.FALLOS_ANTES_DE_BLOQUEO - 1):
            security.registrar_fallo("user")
        self.assertFalse(security.bloqueado("user"))


class TestPasswords(unittest.TestCase):
    def test_corta(self):
        self.assertFalse(security.validar_password("Abc12345")[0])

    def test_comun(self):
        self.assertFalse(security.validar_password("password123")[0])

    def test_solo_letras(self):
        self.assertFalse(security.validar_password("abcdefghijklmno")[0])

    def test_solo_numeros(self):
        self.assertFalse(security.validar_password("12345678901234")[0])

    def test_valida(self):
        ok, motivo = security.validar_password("Caballo7Purpura")
        self.assertTrue(ok, motivo)

    def test_none(self):
        self.assertFalse(security.validar_password(None)[0])


class TestTokens(unittest.TestCase):
    def test_hash_es_estable_y_distinto_del_token(self):
        token = "abc123"
        h = security.hash_token(token)
        self.assertEqual(h, security.hash_token(token))
        self.assertNotEqual(h, token)
        self.assertEqual(len(h), 64)

    def test_vacio(self):
        self.assertEqual(len(security.hash_token("")), 64)

    def test_iguales_seguro(self):
        self.assertTrue(security.iguales_seguro("abc", "abc"))
        self.assertFalse(security.iguales_seguro("abc", "abd"))
        self.assertFalse(security.iguales_seguro("abc", ""))


class TestCabeceras(unittest.TestCase):
    def test_html_lleva_csp_y_no_cache(self):
        resp = _Resp()
        resp.headers["content-type"] = "text/html; charset=utf-8"
        security.aplicar_cabeceras(resp, _Req())
        self.assertIn("default-src", resp.headers["Content-Security-Policy"])
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_json_no_lleva_csp(self):
        resp = _Resp()
        resp.headers["content-type"] = "application/json"
        security.aplicar_cabeceras(resp, _Req())
        self.assertNotIn("Content-Security-Policy", resp.headers)

    def test_hsts_solo_en_https(self):
        https = _Resp()
        security.aplicar_cabeceras(https, _Req(scheme="https"))
        self.assertIn("Strict-Transport-Security", https.headers)

        http = _Resp()
        security.aplicar_cabeceras(http, _Req(scheme="http"))
        self.assertNotIn("Strict-Transport-Security", http.headers)

    def test_no_pisa_lo_que_ya_puso_el_endpoint(self):
        resp = _Resp()
        resp.headers["X-Frame-Options"] = "DENY"
        security.aplicar_cabeceras(resp, _Req())
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")


class TestCookie(unittest.TestCase):
    def test_cookie_endurecida(self):
        resp = _Resp()
        security.poner_cookie_sesion(resp, "token123", _Req(scheme="https"))
        self.assertTrue(resp.cookie["httponly"])
        self.assertTrue(resp.cookie["secure"])
        self.assertIn(resp.cookie["samesite"], ("none", "strict", "lax"))
        self.assertEqual(resp.cookie["path"], "/")

    def test_token_prioriza_cookie(self):
        req = _Req(headers={"Authorization": "Bearer del_header"},
                   cookies={security.COOKIE_NOMBRE: "de_la_cookie"})
        self.assertEqual(security.token_de_request(req), "de_la_cookie")

    def test_sin_cookie_usa_bearer(self):
        req = _Req(headers={"Authorization": "Bearer del_header"})
        self.assertEqual(security.token_de_request(req), "del_header")

    def test_sin_nada(self):
        self.assertEqual(security.token_de_request(_Req()), "")


class TestEventoNoRompe(unittest.TestCase):
    def test_si_la_bd_falla_no_propaga(self):
        with mock.patch("db.registrar_evento_seguridad",
                        side_effect=RuntimeError("bd caida")):
            # No debe lanzar: auditar no puede tumbar la operacion auditada.
            security.evento("login_ok", "ok", usuario="x", request=_Req())


class TestPuertaGlobal(unittest.TestCase):
    """Todo privado por defecto: solo pasa la lista blanca.

    Es la prueba que evita el error clasico: escribir un endpoint nuevo y
    olvidarse de protegerlo. Con esta puerta, uno nuevo nace cerrado.
    """

    @classmethod
    def setUpClass(cls):
        import main as app_main

        cls.m = app_main

    def test_publicas_reales(self):
        """Solo 4. Cada una es una imposibilidad tecnica, no una comodidad."""
        self.assertEqual(self.m.RUTAS_PUBLICAS, {
            "/health", "/auth/signin", "/auth/signup", "/pagadito/return",
        })

    def test_nada_mas_es_publico(self):
        for ruta in ("/", "/models", "/analytics/pv", "/errors/report",
                     "/auth/forgot", "/auth/reset", "/recuperar",
                     "/verificar-email", "/dashboard/picks", "/transactions",
                     "/admin/keys", "/tv/canales-cdn", "/channels",
                     "/live/index.m3u8", "/stats/summary", "/pagadito/debug",
                     "/props", "/parlay", "/track", "/perfil"):
            self.assertFalse(self.m._ruta_publica(ruta), f"{ruta} deberia ser privada")

    def test_recuperacion_cerrada_por_defecto(self):
        """Con la recuperacion apagada no se puede abrir un enlace del correo."""
        if not self.m.PERMITIR_RECUPERACION:
            for ruta in ("/auth/forgot", "/auth/reset", "/recuperar",
                         "/verificar-email"):
                self.assertFalse(self.m._ruta_publica(ruta))

    def test_escaparate_privado_por_defecto(self):
        # "Todo privado" incluye el partido destacado de ESPN salvo que se abra.
        if not self.m.TV_ESCAPARATE_PUBLICO:
            self.assertFalse(self.m._ruta_publica("/tv-live/match"))

    def test_barra_final_no_abre_nada(self):
        self.assertTrue(self.m._ruta_publica("/health/"))
        self.assertFalse(self.m._ruta_publica("/dashboard/picks/"))

    def test_regex_se_deriva_del_dominio_real(self):
        """El regex sale de FRONTEND_ORIGINS: dominio + subdominios, nada mas.

        Es lo que evita el fallo que dejo la web muerta: la lista se calculaba
        con dominios escritos a mano y faltaba el de verdad.
        """
        regex = self.m._regex_desde_origenes(
            ["https://threesixtybets.xyz", "https://otro-vercel.vercel.app"]
        )
        import re

        for permitido in ("https://threesixtybets.xyz",
                          "https://www.threesixtybets.xyz",
                          "https://app.threesixtybets.xyz",
                          "http://localhost:5173"):
            self.assertTrue(re.fullmatch(regex, permitido), f"deberia permitir {permitido}")
        for bloqueado in ("https://evil.com",
                          "https://threesixtybets.xyz.evil.com",
                          "https://otro-vercel.vercel.app",
                          "https://cualquiera.vercel.app",
                          "http://threesixtybets.xyz"):
            self.assertFalse(re.fullmatch(regex, bloqueado), f"NO deberia permitir {bloqueado}")

    def test_401_lleva_cors_del_origen_permitido(self):
        origen = "https://www.threesixtybets.xyz"
        with mock.patch.object(self.m, "frontend_origins", []), \
                mock.patch.object(
                    self.m, "_ORIGENES_REGEX",
                    self.m._regex_desde_origenes(["https://threesixtybets.xyz"])):
            req = _Req(headers={"Origin": origen})
            cabeceras = self.m._respuesta_401(req).headers
        self.assertEqual(cabeceras.get("access-control-allow-origin"), origen)
        self.assertEqual(cabeceras.get("www-authenticate"), "Bearer")

    def test_401_no_da_cors_a_un_origen_ajeno(self):
        with mock.patch.object(self.m, "frontend_origins", []), \
                mock.patch.object(
                    self.m, "_ORIGENES_REGEX",
                    self.m._regex_desde_origenes(["https://threesixtybets.xyz"])):
            req = _Req(headers={"Origin": "https://evil.com"})
            cabeceras = self.m._respuesta_401(req).headers
        self.assertIsNone(cabeceras.get("access-control-allow-origin"))


class TestPuertaTV(unittest.TestCase):
    """La puerta de la TV, con sus dos interruptores separados.

    - `catalogo`: listar canales y resolver el stream. Por defecto CERRADO.
    - `stream`:   segmentos y FFmpeg. Por defecto ABIERTO (necesita que el
      reproductor mande la cookie).

    Se importa `main` de verdad para probar el codigo que corre en produccion.
    """

    @classmethod
    def setUpClass(cls):
        import main as app_main

        cls.m = app_main

    def setUp(self):
        self._cat = self.m.TV_EXIGIR_CATALOGO
        self._str = self.m.TV_EXIGIR_STREAM
        # No escribir en la BD real desde un test.
        self.patch = mock.patch("db.registrar_evento_seguridad", return_value=True)
        self.patch.start()

    def tearDown(self):
        self.m.TV_EXIGIR_CATALOGO = self._cat
        self.m.TV_EXIGIR_STREAM = self._str
        self.patch.stop()

    def test_catalogo_apagado_no_hace_nada(self):
        self.m.TV_EXIGIR_CATALOGO = False
        self.assertIsNone(self.m._exigir_acceso_tv(_Req(), None, "catalogo"))

    def test_catalogo_cerrado_sin_sesion_da_401(self):
        self.m.TV_EXIGIR_CATALOGO = True
        with self.assertRaises(HTTPException) as ctx:
            self.m._exigir_acceso_tv(_Req(), None, "catalogo")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_catalogo_cerrado_sin_acceso_da_403(self):
        self.m.TV_EXIGIR_CATALOGO = True
        with mock.patch("db.es_premium_row", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                self.m._exigir_acceso_tv(_Req(), {"username": "ana"}, "catalogo")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_catalogo_cerrado_con_acceso_pasa(self):
        self.m.TV_EXIGIR_CATALOGO = True
        usuario = {"username": "ana", "access_expires_at": None}
        with mock.patch("db.es_premium_row", return_value=True):
            self.assertEqual(
                self.m._exigir_acceso_tv(_Req(), usuario, "catalogo"), usuario)

    def test_los_dos_interruptores_son_independientes(self):
        """Cerrar el catalogo NO cierra el stream (romperia la TV)."""
        self.m.TV_EXIGIR_CATALOGO = True
        self.m.TV_EXIGIR_STREAM = False
        self.assertIsNone(self.m._exigir_acceso_tv(_Req(), None, "stream"))
        with self.assertRaises(HTTPException):
            self.m._exigir_acceso_tv(_Req(), None, "catalogo")

    def test_stream_cerrado_sin_sesion_da_401(self):
        self.m.TV_EXIGIR_STREAM = True
        with self.assertRaises(HTTPException) as ctx:
            self.m._exigir_acceso_tv(_Req(), None, "stream")
        self.assertEqual(ctx.exception.status_code, 401)


class TestHostsTV(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main as app_main

        cls.m = app_main

    def setUp(self):
        self._permitidos = self.m.TV_HOSTS_PERMITIDOS
        self._publicos = self.m.TV_HOSTS_PUBLICOS

    def tearDown(self):
        self.m.TV_HOSTS_PERMITIDOS = self._permitidos
        self.m.TV_HOSTS_PUBLICOS = self._publicos

    def test_sin_lista_permite_cualquier_host(self):
        self.m.TV_HOSTS_PERMITIDOS = []
        self.assertTrue(self.m._host_permitido("https://loquesea.example/x.m3u8"))

    def test_lista_blanca_permite_el_host_y_sus_subdominios(self):
        self.m.TV_HOSTS_PERMITIDOS = ["cdnlivetv.tv"]
        self.assertTrue(self.m._host_permitido("https://cdnlivetv.tv/a.m3u8"))
        self.assertTrue(self.m._host_permitido("https://edge.cdnlivetv.tv/a.m3u8"))

    def test_lista_blanca_bloquea_el_resto(self):
        self.m.TV_HOSTS_PERMITIDOS = ["cdnlivetv.tv"]
        self.assertFalse(self.m._host_permitido("https://evil.com/a.m3u8"))
        # El truco clasico: el dominio bueno como subcadena.
        self.assertFalse(self.m._host_permitido("https://cdnlivetv.tv.evil.com/a.m3u8"))

    def test_escaparate_publico(self):
        self.m.TV_HOSTS_PUBLICOS = ["espncdn.com"]
        self.assertTrue(self.m._es_publico("https://a.espncdn.com/x.m3u8"))
        self.assertFalse(self.m._es_publico("https://cdnlivetv.tv/x.m3u8"))

    def test_sin_escaparate_nada_es_publico(self):
        self.m.TV_HOSTS_PUBLICOS = []
        self.assertFalse(self.m._es_publico("https://a.espncdn.com/x.m3u8"))


class TestSesionesHasheadas(unittest.TestCase):
    """La tabla `sessions` ya no guarda el token: guarda su hash.

    Se simula la BD con una lista para probar la MIGRACION de las sesiones
    antiguas (en claro) sin depender de MySQL.
    """

    def setUp(self):
        import db

        self.db = db
        self.tabla = []
        self.patch = mock.patch.object(db, "run_query",
                                       side_effect=self._fake_query)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def _fake_query(self, query, params=None, fetchone=False):
        import datetime

        ahora = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        q = " ".join((query or "").split()).lower()
        if q.startswith("insert into sessions"):
            self.tabla.append({"token": params[0], "user_id": params[1]})
            return True
        if q.startswith("select users."):
            for fila in self.tabla:
                if fila["token"] == params[0]:
                    usuario = {"id": "u1", "username": "ana", "role": "user",
                               "credits": 0, "access_expires_at": None,
                               "_session_created_at": ahora,
                               "_session_token": fila["token"]}
                    return usuario
            return None
        if q.startswith("update sessions"):
            for fila in self.tabla:
                if fila["token"] == params[1]:
                    fila["token"] = params[0]
            return True
        if q.startswith("delete from sessions"):
            if "in (" in q:
                self.tabla = [f for f in self.tabla if f["token"] not in params]
            return True
        return True

    def test_el_token_no_se_guarda_en_claro(self):
        token = self.db.create_session("u1")
        self.assertEqual(len(self.tabla), 1)
        self.assertNotEqual(self.tabla[0]["token"], token)
        self.assertEqual(self.tabla[0]["token"], self.db._hash_token(token))
        # Aun asi, el token en claro sirve para autenticarse.
        self.assertIsNotNone(self.db.get_user_by_token(token))

    def test_migra_una_sesion_antigua_sin_echar_al_usuario(self):
        # Sesion creada con el codigo viejo: token EN CLARO en la tabla.
        self.tabla.append({"token": "token_viejo_en_claro", "user_id": "u1"})
        usuario = self.db.get_user_by_token("token_viejo_en_claro")
        self.assertIsNotNone(usuario)               # sigue autenticando
        self.assertEqual(self.tabla[0]["token"],   # y queda migrada
                         self.db._hash_token("token_viejo_en_claro"))

    def test_token_inventado_no_entra(self):
        self.db.create_session("u1")
        self.assertIsNone(self.db.get_user_by_token("token_inventado"))

    def test_vacio_no_entra(self):
        self.assertIsNone(self.db.get_user_by_token(""))


if __name__ == "__main__":
    unittest.main()


