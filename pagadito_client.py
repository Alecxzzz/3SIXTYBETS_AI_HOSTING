"""
Cliente Pagadito WSPG en Python puro (no hay SDK oficial de Python).

El WSPG de Pagadito es un servicio SOAP estilo RPC/encoded (NuSOAP).
Verificado en vivo contra el WSDL oficial:
    https://sandbox.pagadito.com/comercios/wspg/charges.php?wsdl

Operaciones usadas (todas devuelven un string JSON {"code","message","value"}):
    connect(uid, wsk, format_return)
    exec_trans(token, ern, amount, details, format_return, currency,
               custom_params, allow_pending_payments)
    get_status(token, token_trans, format_return)

Variables de entorno:
    PAGADITO_UID        UID del comercio (obligatorio)
    PAGADITO_WSK        WSK del comercio (obligatorio)
    PAGADITO_SANDBOX    "true"/"1" para modo sandbox (default true)
"""

import json
import os
import xml.etree.ElementTree as ET

import requests


# ============================================================
# URLs del WSPG (sandbox por defecto para pruebas)
# ============================================================
WSPG_SANDBOX_URL = "https://sandbox.pagadito.com/comercios/wspg/charges.php"
WSPG_PRODUCTION_URL = "https://comercios.pagadito.com/wspg/charges.php"

# Codigo de respuesta -> descripcion (lista oficial del APIPG)
RESPONSE_CODES = {
    "PG1001": "Conexion exitosa.",
    "PG1002": "Fallo de conexion / URL de checkout (segun operacion).",
    "PG1003": "Transaccion registrada correctamente.",
    "PG1004": "Transaccion consultada correctamente.",
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
    """Error generico al hablar con el APIPG de Pagadito."""

    def __init__(self, message, code=None, message_pg=None):
        self.code = code
        self.message_pg = message_pg
        super().__init__(message)


class PagaditoConnectionError(PagaditoError):
    """No se pudo conectar / Pagadito no disponible (timeout, red, PG2001)."""


class PagaditoAuthError(PagaditoError):
    """UID/WSK invalidos o token de conexion expirado (PG1002 en connect)."""


class PagaditoTransactionError(PagaditoError):
    """La operacion fue rechazada por parametros/monto (PG3003, PG3007...)."""


def _describe(code: str) -> str:
    return RESPONSE_CODES.get(code, "Codigo desconocido.")


class PagaditoClient:
    """Cliente del APIPG de Pagadito (protocolo del SDK PHP oficial)."""

    def __init__(self, uid=None, wsk=None, sandbox=None, timeout=20):
        self.uid = uid or os.environ.get("PAGADITO_UID", "")
        self.wsk = wsk or os.environ.get("PAGADITO_WSK", "")
        if sandbox is None:
            sandbox = os.environ.get("PAGADITO_SANDBOX", "true").strip().lower() in (
                "1", "true", "yes", "on",
            )
        self.sandbox = bool(sandbox)
        self.timeout = timeout
        self.token = None

        if not self.uid or not self.wsk:
            raise PagaditoAuthError(
                "Faltan PAGADITO_UID / PAGADITO_WSK en las variables de entorno."
            )

    # --------------------------------------------------------
    # Transporte
    # --------------------------------------------------------
    @property
    def endpoint(self) -> str:
        return WSPG_SANDBOX_URL if self.sandbox else WSPG_PRODUCTION_URL

    @staticmethod
    def _xml_escape(value: str) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _soap_envelope(self, operation: str, params: dict) -> str:
        items = "".join(
            f'<{key} xsi:type="xsd:string">{self._xml_escape(value)}</{key}>'
            for key, value in params.items()
            if value is not None
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<SOAP-ENV:Envelope '
            'SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
            'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" '
            'xmlns:tns="urn:wspg">'
            f'<SOAP-ENV:Body><tns:{operation}>{items}</tns:{operation}>'
            '</SOAP-ENV:Body></SOAP-ENV:Envelope>'
        )

    def _call(self, operation: str, params: dict) -> dict:
        """Ejecuta una operacion SOAP del WSPG y devuelve {"code","message","value"}."""
        envelope = self._soap_envelope(operation, params)
        try:
            response = requests.post(
                self.endpoint,
                data=envelope.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f"urn:ws#{operation}",
                },
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise PagaditoConnectionError(
                "Timeout: Pagadito no respondio a tiempo."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise PagaditoConnectionError(
                f"No se pudo conectar con Pagadito: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise PagaditoConnectionError(
                f"Pagadito respondio HTTP {response.status_code}."
            )
        if response.status_code != 200:
            raise PagaditoConnectionError(
                f"Pagadito respondio HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

        # Extraer la falla SOAP si la hay.
        if "SOAP-ENV:Fault" in response.text or ":Fault" in response.text:
            raise PagaditoConnectionError(
                f"Falla SOAP de Pagadito: {response.text[:300]}"
            )

        # Extraer el valor de <return> del cuerpo SOAP.
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise PagaditoConnectionError(
                f"Respuesta no-XML de Pagadito: {response.text[:200]}"
            ) from exc

        return_text = None
        for element in root.iter():
            if element.tag.endswith("return"):
                return_text = element.text or ""
                break
        if return_text is None:
            raise PagaditoConnectionError(
                f"Respuesta SOAP sin 'return': {response.text[:200]}"
            )

        try:
            data = json.loads(return_text)
        except (ValueError, TypeError) as exc:
            raise PagaditoConnectionError(
                f"El WSPG devolvio una respuesta no-JSON: {return_text[:200]}"
            ) from exc

        if not isinstance(data, dict) or "code" not in data:
            raise PagaditoConnectionError(
                f"Respuesta inesperada de Pagadito: {str(data)[:200]}"
            )
        return data

    @staticmethod
    def _raise_for(response: dict):
        code = str(response.get("code", ""))
        value = response.get("value", "")
        message = response.get("message", "")
        detail = f"{message} (value={value!r})" if message else f"(value={value!r})"
        if code == "PG2001":
            raise PagaditoConnectionError(_describe(code), code=code)
        if code in ("PG1002", "PG3001", "PG3002"):
            raise PagaditoAuthError(
                f"{_describe(code)} {detail}", code=code, message_pg=value
            )
        if code in ("PG2002", "PG3003", "PG3004", "PG3005", "PG3006",
                    "PG3007", "PG3008"):
            raise PagaditoTransactionError(
                f"{_describe(code)} {detail}", code=code, message_pg=value
            )

    def _call_with_reconnect(self, operation: str, extra: dict) -> dict:
        if not self.token:
            self.connect()
        response = self._call(operation, {"token": self.token, **extra})
        # Token expirado/invalidado (PG3002 o PG3007): reconectar y reintentar.
        if str(response.get("code")) in ("PG3002", "PG3007"):
            self.token = None
            self.connect()
            response = self._call(operation, {"token": self.token, **extra})
        return response

    # --------------------------------------------------------
    # Operaciones
    # --------------------------------------------------------
    def connect(self) -> str:
        """
        Autentica el comercio contra el APIPG y guarda el token de conexion.
        Devuelve el token.
        """
        response = self._call("connect", {
            "uid": self.uid,
            "wsk": self.wsk,
            "format_return": "json",
        })
        code = str(response.get("code", ""))
        if code != "PG1001":
            self._raise_for(response)
            raise PagaditoAuthError(
                f"Conexion fallida: {_describe(code)}", code=code
            )
        self.token = str(response.get("value", ""))
        if not self.token:
            raise PagaditoAuthError("Pagadito no devolvio token de conexion.")
        return self.token

    def exec_trans(self, amount, ern, details=None, currency="USD") -> str:
        """
        Registra la transaccion en Pagadito y devuelve la URL de checkout.

        amount:   monto total en la moneda indicada.
        ern:      ERN (External Reference Number): ID unico de tu orden.
        details:  lista de dicts {"quantity", "description", "price",
                  "url_product"} (recomendado; el monto debe coincidir con
                  la suma quantity*price).
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

        # Detalles como arreglo de OBJETOS JSON (claves quantity, description,
        # price, url_product) segun la especificacion oficial del WSPG.
        details_json = []
        for detail in details or []:
            details_json.append({
                "quantity": int(detail.get("quantity", 1)),
                "description": str(detail.get("description", "Producto"))[:255],
                "price": round(float(detail.get("price", 0)), 2),
                "url_product": str(detail.get("url_product", "")),
            })

        # Cada transaccion usa una conexion fresca: el WSPG invalida el token
        # tras registrar un cobro (observado en sandbox).
        self.connect()
        response = self._call("exec_trans", {
            "token": self.token,
            "ern": str(ern),
            "amount": f"{amount:.2f}",
            "details": json.dumps(details_json),
            "format_return": "json",
            "currency": currency,
            "custom_params": json.dumps([]),
            "allow_pending_payments": "false",
        })
        code = str(response.get("code", ""))
        value = str(response.get("value", ""))

        # El SDK PHP oficial trata PG1002 (con URL) como exito en exec_trans;
        # algunas versiones del APIPG devuelven PG1003.
        if code in ("PG1002", "PG1003") and value.lower().startswith("https://"):
            return value

        self._raise_for(response)
        raise PagaditoTransactionError(
            f"Pagadito no devolvio URL de checkout: {value!r} [{code}]"
        )

    def get_status(self, token_trans) -> dict:
        """
        Consulta el estado de una transaccion por su token.

        Devuelve un dict con "status" (COMPLETED / REGISTERED / CANCELED /
        EXPIRED / REJECTED), "reference", "date_trans", "value" y "raw".
        """
        if not token_trans:
            raise PagaditoTransactionError("Falta el token de la transaccion.")

        response = self._call_with_reconnect(
            "get_status", {"token_trans": str(token_trans), "format_return": "json"}
        )
        self._raise_for(response)

        raw_value = response.get("value", "")
        status_data = {}
        if isinstance(raw_value, str) and raw_value:
            try:
                status_data = json.loads(raw_value)
            except (ValueError, TypeError):
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
