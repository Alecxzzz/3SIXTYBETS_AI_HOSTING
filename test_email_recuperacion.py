# -*- coding: utf-8 -*-
"""
Tests de la recuperacion de contrasena por email (enlace magico).

Lo que se protege:
1. /auth/forgot responde SIEMPRE lo mismo: no se puede usar para averiguar que
   emails tienen cuenta, ni crea token si el email no esta verificado.
2. /auth/reset exige un token valido (un solo uso, caduca) y, al usarlo, cambia
   la contrasena, marca el email como verificado y CIERRA TODAS las sesiones.
3. /auth/change-password exige la contrasena actual y renueva la sesion.
4. /auth/set-email rechaza emails invalidos y emails ya usados por otra cuenta.
5. emailer degrada (devuelve False, no lanza) sin proveedor o sin credencial.
"""
import unittest
from unittest import mock

import emailer
import main


class FakeRequest:
    """Request minima para _ip_del_cliente() y base_url."""

    def __init__(self, ip="1.2.3.4", base="https://api.3sixtybets.com/"):
        self.headers = {"x-forwarded-for": ip}
        self.client = type("Cliente", (), {"host": ip})()
        self.base_url = base


USUARIO = {
    "id": "u1",
    "username": "brayan",
    "password_hash": "salt:hash",
    "role": "user",
    "credits": 0,
    "email": "brayan@example.com",
    "email_verified_at": object(),
    "access_expires_at": None,
}


class Base(unittest.TestCase):
    def setUp(self):
        # El limitador es por proceso: sin limpiarlo, los tests se frenarian
        # entre ellos con un 429.
        main._rate_bucket.clear()


class ForgotTest(Base):
    def _forgot(self, email, usuario):
        with mock.patch.object(main.db, "get_user_by_email", return_value=usuario), \
                mock.patch.object(main.db, "create_email_token",
                                  return_value=("tok123", None)) as crear, \
                mock.patch.object(main, "_enviar_correo_reset",
                                  return_value=True) as enviar:
            res = main.auth_forgot(main.ForgotIn(email=email), FakeRequest())
        return res, crear, enviar

    def test_email_desconocido_responde_igual_y_no_crea_token(self):
        res, crear, enviar = self._forgot("nadie@example.com", None)

        self.assertTrue(res["ok"])
        self.assertIn("Si esa direccion tiene una cuenta", res["mensaje"])
        crear.assert_not_called()
        enviar.assert_not_called()

    def test_email_sin_verificar_no_recibe_enlace(self):
        _, crear, enviar = self._forgot("brayan@example.com",
                                        dict(USUARIO, email_verified_at=None))

        crear.assert_not_called()
        enviar.assert_not_called()

    def test_email_verificado_recibe_enlace_con_token(self):
        _, crear, enviar = self._forgot("brayan@example.com", USUARIO)

        crear.assert_called_once()
        self.assertEqual(crear.call_args.args[1], "reset")
        self.assertEqual(crear.call_args.args[2], main.RESET_TOKEN_MIN)
        enlace = enviar.call_args.args[2]
        self.assertEqual(enlace, "https://api.3sixtybets.com/recuperar?token=tok123")

    def test_email_invalido_no_consulta_la_bd(self):
        with mock.patch.object(main.db, "get_user_by_email") as buscar:
            res = main.auth_forgot(main.ForgotIn(email="no-es-un-email"), FakeRequest())

        buscar.assert_not_called()
        self.assertTrue(res["ok"])


class ResetTest(Base):
    def test_token_invalido_da_400(self):
        with mock.patch.object(main.db, "consume_email_token", return_value=None):
            with self.assertRaises(main.HTTPException) as ctx:
                main.auth_reset(main.ResetIn(token="malo", password="nueva123"), FakeRequest())

        self.assertEqual(ctx.exception.status_code, 400)

    def test_password_corta_da_400_sin_tocar_el_token(self):
        with mock.patch.object(main.db, "consume_email_token") as consumir:
            with self.assertRaises(main.HTTPException) as ctx:
                main.auth_reset(main.ResetIn(token="tok", password="123"), FakeRequest())

        self.assertEqual(ctx.exception.status_code, 400)
        consumir.assert_not_called()

    def test_token_valido_cambia_clave_y_cierra_sesiones(self):
        with mock.patch.object(main.db, "consume_email_token",
                               return_value={"user_id": "u1"}) as consumir, \
                mock.patch.object(main.db, "set_password", return_value=True) as guardar, \
                mock.patch.object(main.db, "marcar_email_verificado") as verificar, \
                mock.patch.object(main.db, "delete_sessions_for_user") as cerrar:
            res = main.auth_reset(main.ResetIn(token="tok", password="nueva123"), FakeRequest())

        self.assertTrue(res["ok"])
        consumir.assert_called_once_with("tok", "reset")
        self.assertEqual(guardar.call_args.args[0], "u1")
        self.assertEqual(guardar.call_args.args[1], "nueva123")
        verificar.assert_called_once_with("u1")
        cerrar.assert_called_once_with("u1")


class ChangePasswordTest(Base):
    def test_actual_incorrecta_da_400(self):
        with mock.patch.object(main.db, "verify_password", return_value=False), \
                mock.patch.object(main.db, "set_password") as guardar:
            with self.assertRaises(main.HTTPException) as ctx:
                main.auth_change_password(
                    main.ChangePasswordIn(current_password="mala", new_password="nueva123"),
                    FakeRequest(), USUARIO,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        guardar.assert_not_called()

    def test_cambio_ok_cierra_sesiones_y_renueva_la_actual(self):
        with mock.patch.object(main.db, "verify_password", return_value=True), \
                mock.patch.object(main.db, "set_password", return_value=True) as guardar, \
                mock.patch.object(main.db, "delete_sessions_for_user") as cerrar, \
                mock.patch.object(main.db, "create_session", return_value="tok-nuevo") as nueva:
            res = main.auth_change_password(
                main.ChangePasswordIn(current_password="actual", new_password="nueva123"),
                FakeRequest(), USUARIO,
            )

        self.assertEqual(res["access_token"], "tok-nuevo")
        self.assertEqual(guardar.call_args.args[0], "u1")
        cerrar.assert_called_once_with("u1")
        nueva.assert_called_once_with("u1")


class SetEmailTest(Base):
    def test_email_invalido_da_400(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.auth_set_email(main.EmailIn(email="arroba-no"), FakeRequest(), USUARIO)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_email_de_otra_cuenta_da_400(self):
        with mock.patch.object(main.db, "get_user_by_email", return_value={"id": "u2"}):
            with self.assertRaises(main.HTTPException) as ctx:
                main.auth_set_email(main.EmailIn(email="otro@example.com"),
                                    FakeRequest(), USUARIO)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_guarda_email_y_manda_verificacion(self):
        with mock.patch.object(main.db, "get_user_by_email", return_value=None), \
                mock.patch.object(main.db, "set_user_email", return_value=True) as guardar, \
                mock.patch.object(main.db, "create_email_token",
                                  return_value=("tok-verif", None)), \
                mock.patch.object(main, "_enviar_correo_verificacion",
                                  return_value=True) as enviar:
            res = main.auth_set_email(main.EmailIn(email="Nuevo@Example.com"),
                                      FakeRequest(), USUARIO)

        self.assertTrue(res["correo_enviado"])
        self.assertEqual(guardar.call_args.args[1], "nuevo@example.com")
        self.assertEqual(enviar.call_args.args[2],
                         "https://api.3sixtybets.com/verificar-email?token=tok-verif")


class EmailerTest(unittest.TestCase):
    def test_sin_proveedor_no_envia_y_no_revienta(self):
        with mock.patch.dict("os.environ", {"EMAIL_PROVIDER": "none"}, clear=False):
            self.assertFalse(emailer.configurado())
            self.assertFalse(emailer.send_email("a@b.com", "x", "<p>x</p>"))

    def test_proveedor_sin_credencial_no_envia(self):
        entorno = {"EMAIL_PROVIDER": "resend",
                   "EMAIL_FROM": "no-reply@3sixtybets.com",
                   "RESEND_API_KEY": ""}
        with mock.patch.dict("os.environ", entorno, clear=False):
            self.assertFalse(emailer.configurado())
            self.assertFalse(emailer.send_email("a@b.com", "x", "<p>x</p>"))

    def test_resend_usa_la_key_y_el_remitente(self):
        entorno = {"EMAIL_PROVIDER": "resend",
                   "EMAIL_FROM": "3SIXTYBETS <no-reply@3sixtybets.com>",
                   "RESEND_API_KEY": "rk_test"}
        with mock.patch.dict("os.environ", entorno, clear=False), \
                mock.patch.object(emailer.requests, "post") as post:
            post.return_value = mock.Mock(status_code=200, text="ok")
            self.assertTrue(emailer.send_email("a@b.com", "Asunto", "<p>hola</p>", "hola"))

        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer rk_test")
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["a@b.com"])
        self.assertIn("no-reply@3sixtybets.com", post.call_args.kwargs["json"]["from"])

    def test_resend_500_devuelve_false(self):
        entorno = {"EMAIL_PROVIDER": "resend",
                   "EMAIL_FROM": "no-reply@3sixtybets.com",
                   "RESEND_API_KEY": "rk_test"}
        with mock.patch.dict("os.environ", entorno, clear=False), \
                mock.patch.object(emailer.requests, "post") as post:
            post.return_value = mock.Mock(status_code=500, text="boom")
            self.assertFalse(emailer.send_email("a@b.com", "x", "<p>x</p>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
