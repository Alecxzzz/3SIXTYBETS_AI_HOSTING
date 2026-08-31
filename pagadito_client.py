"""
Cliente Pagadito WSPG en Python puro (no hay SDK oficial de Python).

Habla directamente con el servicio web de Pagadito (WSPG) igual que el
SDK PHP oficial: POST form-encoded al endpoint charges.php con tres
campos: operacion, token y vars (base64 de un array serializado estilo
PHP). Se pide la respuesta en formato JSON (format_return = "json").

Referencia: https://dev.pagadito.com/index.php?mod=docs&hac=mostrar&tema=APIPG

Variables de entorno:
    PAGADITO_UID        UID del comercio (obligatorio)
    PAGADITO_WSK        WSK del comercio (obligatorio)
    PAGADITO_SANDBOX    "true"/"1" para modo sandbox (default true)
"""

import base64
import json
import os
import re
from decimal import Decimal

import requests


# ============================================================
# URLs del WSPG (sandbox por defecto para pruebas)
# ============================================================
WSPG_SANDBOX_URL = "https://sandbox.pagadito.com/comercios/wspg/charges.php"
WSPG_PRODUCTION_URL = "https://comercios.pagadito.com/wspg/charges.php"

# Codigo de respuesta -> descripcion (lista oficial del WSPG)
RESPONSE_CODES = {
    "PG1001": "Conexion exitosa.",
    "PG1002": "Fallo de conexion: UID/WSK invalidos o conexion denegada.",
    "PG1003": "Transaccion registrada correctamente.",
    "PG1004": "Transaccion consultada correctamente.",
    "PG1005": "Tipo de cambio consultado correctamente.",
    "PG2001": "Pagadito no esta disponible en este momento.",
    "PG2002": "Operacion no definida por el comercio.",
    "PG3001": "El comercio no tiene acceso a esta operacion.",
    "PG3002": "Token de conexion invalido o expirado.",
    "PG3003": "Monto insuficiente para procesar la transaccion.",
    "PG3004": "Monto a cambiar insuficiente.",
    "PG3005": "La transaccion no existe.",
    "PG3006": "Parametros faltantes.",
    "PG3007": "Parametros invalidos.",
    "PG3008": "Moneda no soportada.",
}


class PagaditoError(Exception):
    """Error generico al hablar con el WSPG de Pagadito."""

    def __init__(self, message, code=None, message_pg=None):
        self.code = code
        self.message_pg = message_pg
        super().__init__(message)


class PagaditoConnectionError(PagaditoError):
    """No se pudo conectar / Pagadito no disponible (timeout, red, PG2001)."""


class PagaditoAuthError(PagaditoError):
    """UID/WSK invalidos o token de conexion expirado (PG1002, PG3002)."""


class PagaditoTransactionError(PagaditoError):
    """La operacion fue rechazada por parametros/monto (PG3003, PG3007...)."""
    """La operacion fue rechazada por parametros/monto (PG3003, PG3007...)."""


def _php_serialize(value) -> str:
    """
    Serializador minimalista al formato serialize() de PHP, suficiente
    para el unico tipo de dato que el WSPG espera en "vars":
    arrays numericos de strings/numeros (y arrays anidados de estos).
    """
    if isinstance(value, bool):
        return f"b:{1 if value else 0};"
    if isinstance(value, int):
        return f"i:{value};"
    if isinstance(value, (float, Decimal)):
        return f"d:{value};"
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return f's:{len(raw)}:"{value}";'
    if isinstance(value, (list, tuple)):
        parts = []
        for index, item in enumerate(value):
            parts.append(f"i:{index};")
            parts.append(_php_serialize(item))
        return f"a:{len(value)}:{{{''.join(parts)}}}"
    raise PagaditoError(f"Tipo no serializable para Pagadito: {type(value)!r}")


def _php_unserialize(text: str):
    """
    Decodificador minimalista del formato serialize() de PHP. Solo soporta
    lo que el WSPG devuelve: arrays anidados, strings, enteros y doubles.
    """

    def parse(p):
        if text.startswith("N;", p):
            return None, p + 2
        if text[p] == "a":
            # a:N:{ ... }
            colon = text.index(":", p + 1)
            colon2 = text.index(":", colon + 1)
            count = int(text[colon + 1 : colon2])
            open_brace = text.index("{", colon2)
            p = open_brace + 1
            items = {}
            for _ in range(count):
                # --- clave ---
                if text[p] == "i":
                    end = text.index(";", p)
                    key = int(text[p + 2 : end])
                    p = end + 1
                elif text[p] == "s":
                    colon_k = text.index(":", p + 1)
                    klen = int(text[p + 2 : colon_k])
                    key_start = text.index('"', colon_k) + 1
                    key = text[key_start : key_start + klen]
                    p = key_start + klen + 2  # saltar comilla de cierre y ";"
                else:
                    raise ValueError(f"clave no soportada en posicion {p}")
                # --- valor ---
                items[key], p = parse(p)
            # devolver lista si todas las claves son numericas consecutivas
            if items and set(items.keys()) == set(range(count)):
                return [items[i] for i in range(count)], p + 1  # saltar "}"
            return items, p + 1  # saltar "}"
        if text[p] == "s":
            colon = text.index(":", p + 2)
            length = int(text[p + 2 : colon])
            start = text.index('"', colon) + 1
            return text[start : start + length], start + length + 2
        if text[p] == "i":
            end = text.index(";", p)
            return int(text[p + 2 : end]), end + 1
        if text[p] == "d":
            end = text.index(";", p)
            raw = text[p + 2 : end]
            value = float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
            return value, end + 1
        raise ValueError(f"token inesperado en posicion {p}")

    try:
        value, _ = parse(0)
        return value
    except Exception as exc:
        raise PagaditoError(f"Respuesta ilegible de Pagadito: {exc}") from exc


class PagaditoClient:
    """
    Cliente del WSPG de Pagadito.

    Maneja internamente el token de conexion (connect) y lo reutiliza
    entre llamadas; se reconecta automaticamente si el token expira
    (PG3002).
    """

    def __init__(self, uid=None, wsk=None, sandbox=None, timeout=15):
        self.uid = uid or os.getenv("PAGADITO_UID", "")
        self.wsk = wsk or os.getenv("PAGADITO_WSK", "")
        if sandbox is None:
            sandbox = os.getenv("PAGADITO_SANDBOX", "true").strip().lower() in (
                "1", "true", "yes",
            )
        self.sandbox = bool(sandbox)
        self.url = WSPG_SANDBOX_URL if self.sandbox else WSPG_PRODUCTION_URL
        self.timeout = timeout
        self.token = None

        if not self.uid or not self.wsk:
            raise PagaditoError(
                "Faltan PAGADITO_UID / PAGADITO_WSK en las variables de entorno."
            )

    # ---------------- capa HTTP ----------------

    def _call(self, operation, params, token=""):
        """
        Llama al WSPG. params es una lista de "grupos" de parametros
        (igual que el SDK PHP): [[v1], [v2, v3], ...].
        Devuelve el dict decodificado: {"code","message","value",...}.
        """
        serialized = base64.b64encode(
            _php_serialize(params).encode("utf-8")
        ).decode("ascii")

        try:
            response = requests.post(
                self.url,
                data={"operacion": operation, "token": token, "vars": serialized},
                headers={"User-Agent": "3SIXTYBETS/1.0", "Connection": "close"},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise PagaditoConnectionError(
                "Pagadito no respondio a tiempo (timeout). Intenta de nuevo."
            ) from exc
        except requests.ConnectionError as exc:
            raise PagaditoConnectionError(
                "No se pudo conectar con Pagadito. Revisa tu conexion o el estado del servicio."
            ) from exc
        except requests.RequestException as exc:
            raise PagaditoConnectionError(f"Error de red con Pagadito: {exc}") from exc

        if response.status_code != 200:
            raise PagaditoConnectionError(
                f"Pagadito respondio HTTP {response.status_code}."
            )

        body = (response.text or "").strip()
        if not body:
            raise PagaditoConnectionError("Pagadito devolvio una respuesta vacia.")

        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            try:
                parsed = _php_unserialize(body)
            except PagaditoError:
                parsed = self._parse_xml(body)

        if not isinstance(parsed, dict) or "code" not in parsed:
            raise PagaditoConnectionError(
                f"Respuesta con formato inesperado de Pagadito: {body[:200]}"
            )
        return parsed

    @staticmethod
    def _parse_xml(body):
        """Fallback por si el WSPG responde en XML a pesar de pedir JSON."""

        def grab(tag):
            match = re.search(
                rf"<{tag}>(.*?)</{tag}>", body, re.DOTALL | re.IGNORECASE
            )
            return match.group(1).strip() if match else ""

        return {"code": grab("code"), "message": grab("message"), "value": grab("value")}

    @staticmethod
    def _raise_for(response):
        code = response.get("code", "")
        message = response.get("message", "")
        description = RESPONSE_CODES.get(code, message or "Error desconocido de Pagadito.")

        if code in ("PG1001", "PG1003", "PG1004", "PG1005"):
            return

        kwargs = {"code": code, "message_pg": message}
        if code in ("PG1002", "PG3002"):
            raise PagaditoAuthError(f"{description} [{code}]", **kwargs)
        if code in ("PG2001", "PG2002", "PG3001", "PG3005", "PG3006", "PG3008"):
            raise PagaditoConnectionError(f"{description} [{code}]", **kwargs)
        # PG3003, PG3004, PG3007 y cualquier otro -> error de transaccion
        raise PagaditoTransactionError(f"{description} [{code}]", **kwargs)

    # ---------------- operaciones ----------------

    def connect(self) -> str:
        """
        Autentica UID + WSK contra el WSPG y guarda el token de conexion.
        Levanta PagaditoAuthError si las credenciales son invalidas.
        """
        response = self._call("connect", [[self.uid], [self.wsk], ["json"]])
        self._raise_for(response)
        self.token = response.get("value", "")
        if not self.token:
            raise PagaditoAuthError("Pagadito no devolvio token de conexion.")
        return self.token

    def _call_with_reconnect(self, operation, params):
        if not self.token:
            self.connect()
        response = self._call(operation, params, token=self.token)
        if response.get("code") == "PG3002":
            # Token expirado -> reconectar y reintentar una vez.
            self.connect()
            response = self._call(operation, params, token=self.token)
        return response

    def exec_trans(self, amount, ern, details=None, currency="USD") -> str:
        """
        Registra la transaccion en Pagadito y devuelve la URL de checkout.

        amount:   monto total en la moneda indicada (float/Decimal/str).
        ern:      ERN (External Reference Number): ID unico de tu orden.
        details:  lista de dicts {"quantity", "description", "price",
                  "url_product"} (opcional pero recomendado).
        currency: "USD" por defecto.
        """
        if not ern:
            raise PagaditoTransactionError("El ERN (ID de orden) es obligatorio.")

        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise PagaditoTransactionError(f"Monto invalido: {amount!r}") from exc
        if amount <= 0:
            raise PagaditoTransactionError("El monto debe ser mayor que cero.")

        serialized_details = []
        for detail in details or []:
            serialized_details.append(
                [
                    int(detail.get("quantity", 1)),
                    str(detail.get("description", "Producto"))[:255],
                    round(float(detail.get("price", 0)), 2),
                    str(detail.get("url_product", "")),
                ]
            )

        params = [
            [currency],
            [f"{amount:.2f}"],
            serialized_details,
            [str(ern)],
            ["json"],
        ]

        response = self._call_with_reconnect("exec_trans", params)
        self._raise_for(response)

        value = str(response.get("value", ""))
        if not value.lower().startswith("https://"):
            raise PagaditoTransactionError(
                f"Pagadito no devolvio URL de checkout: {value!r} [{response.get('code')}]"
            )
        return value

    def get_status(self, token_trans) -> dict:
        """
        Consulta el estado de una transaccion por su token.

        Devuelve un dict:
            {
              "status": "COMPLETED" | "REGISTERED" | "CANCELED" | "EXPIRED" | "REJECTED",
              "reference": referencia de la transaccion (si COMPLETED),
              "date_trans": fecha/hora de la transaccion,
              "value": monto,
              "raw": respuesta completa del WSPG,
            }
        """
        if not token_trans:
            raise PagaditoTransactionError("Falta el token de la transaccion.")

        response = self._call_with_reconnect(
            "get_status", [[str(token_trans)], ["json"]]
        )
        self._raise_for(response)

        raw_value = response.get("value", "")
        status_data = {}
        if isinstance(raw_value, str) and raw_value:
            try:
                status_data = json.loads(raw_value)
            except (ValueError, TypeError):
                try:
                    status_data = _php_unserialize(raw_value)
                except PagaditoError:
                    status_data = {}
        elif isinstance(raw_value, dict):
            status_data = raw_value

        return {
            "status": str(status_data.get("status", "")).upper(),
            "reference": status_data.get("reference", ""),
            "date_trans": status_data.get("date_trans", ""),
            "value": status_data.get("value", ""),
            "raw": status_data or raw_value,
        }

