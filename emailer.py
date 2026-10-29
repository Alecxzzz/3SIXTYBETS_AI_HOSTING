"""
Envio de emails transaccionales: recuperacion de contrasena (enlace magico) y
verificacion de direccion.

Proveedor elegido con EMAIL_PROVIDER:
    resend : API HTTPS de Resend   -> RESEND_API_KEY
    brevo  : API HTTPS de Brevo    -> BREVO_API_KEY
    smtp   : SMTP clasico (Gmail con App Password, Zoho, etc.)
             -> SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_TLS
    none   : no envia; escribe el correo en el log (local y tests)

Comunes:
    EMAIL_FROM       "3SIXTYBETS <no-reply@tudominio.com>"  (obligatorio)
    EMAIL_FROM_NAME  nombre visible si EMAIL_FROM es solo la direccion
    EMAIL_REPLY_TO   opcional
    EMAIL_TIMEOUT    segundos (por defecto 15)

Por que degrada en vez de lanzar: si falta la credencial o el proveedor esta
caido, el usuario tiene que poder seguir usando la app. send_email() devuelve
True/False y lo deja en el log; ningun endpoint responde 500 por un fallo de
correo (mismo criterio que el resto del proyecto con las APIs externas).
"""

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import requests


def _timeout() -> int:
    try:
        return max(3, int(os.getenv("EMAIL_TIMEOUT", "15")))
    except ValueError:
        return 15


def provider() -> str:
    return (os.getenv("EMAIL_PROVIDER", "none") or "none").strip().lower()


def _remitente() -> tuple:
    """(nombre, direccion) del remitente a partir de EMAIL_FROM."""
    bruto = (os.getenv("EMAIL_FROM", "") or "").strip()
    nombre, direccion = parseaddr(bruto)
    if not direccion:
        direccion, nombre = bruto, ""
    return (os.getenv("EMAIL_FROM_NAME", "").strip() or nombre), direccion


def configurado() -> bool:
    """True si hay proveedor y credencial suficiente para intentar enviar."""
    p = provider()
    _, direccion = _remitente()
    if p == "none" or not direccion:
        return False
    if p == "resend":
        return bool(os.getenv("RESEND_API_KEY", "").strip())
    if p == "brevo":
        return bool(os.getenv("BREVO_API_KEY", "").strip())
    if p == "smtp":
        return bool(os.getenv("SMTP_HOST", "").strip())
    return False


def _enviar_resend(destino, asunto, html, texto) -> bool:
    nombre, direccion = _remitente()
    cuerpo = {
        "from": formataddr((nombre, direccion)) if nombre else direccion,
        "to": [destino],
        "subject": asunto,
        "html": html,
    }
    if texto:
        cuerpo["text"] = texto
    reply = (os.getenv("EMAIL_REPLY_TO", "") or "").strip()
    if reply:
        cuerpo["reply_to"] = reply
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '').strip()}",
            "Content-Type": "application/json",
        },
        json=cuerpo,
        timeout=_timeout(),
    )
    if r.status_code >= 300:
        print(f"[emailer] resend {r.status_code}: {r.text[:300]}", flush=True)
        return False
    return True


def _enviar_brevo(destino, asunto, html, texto) -> bool:
    nombre, direccion = _remitente()
    cuerpo = {
        "sender": {"email": direccion, "name": nombre or "3SIXTYBETS"},
        "to": [{"email": destino}],
        "subject": asunto,
        "htmlContent": html,
    }
    if texto:
        cuerpo["textContent"] = texto
    reply = (os.getenv("EMAIL_REPLY_TO", "") or "").strip()
    if reply:
        cuerpo["replyTo"] = {"email": reply}
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": os.getenv("BREVO_API_KEY", "").strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=cuerpo,
        timeout=_timeout(),
    )
    if r.status_code >= 300:
        print(f"[emailer] brevo {r.status_code}: {r.text[:300]}", flush=True)
        return False
    return True


def _enviar_smtp(destino, asunto, html, texto) -> bool:
    nombre, direccion = _remitente()
    host = os.getenv("SMTP_HOST", "").strip()
    puerto = int(os.getenv("SMTP_PORT", "587"))
    usuario = os.getenv("SMTP_USER", "").strip() or direccion
    clave = os.getenv("SMTP_PASSWORD", "").strip()
    usa_tls = (os.getenv("SMTP_TLS", "true") or "true").strip().lower() not in ("0", "false", "no")

    mensaje = EmailMessage()
    mensaje["From"] = formataddr((nombre, direccion)) if nombre else direccion
    mensaje["To"] = destino
    mensaje["Subject"] = asunto
    reply = (os.getenv("EMAIL_REPLY_TO", "") or "").strip()
    if reply:
        mensaje["Reply-To"] = reply
    mensaje.set_content(texto or "Abre este correo en un cliente con HTML.")
    mensaje.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, puerto, timeout=_timeout()) as smtp:
        if usa_tls:
            smtp.starttls()
        if usuario and clave:
            smtp.login(usuario, clave)
        smtp.send_message(mensaje)
    return True


def send_email(destino: str, asunto: str, html: str, texto: str = "") -> bool:
    """Envia un correo. Devuelve True si el proveedor lo acepto.

    Nunca lanza: cualquier fallo (sin proveedor, sin credencial, timeout, 4xx/5xx
    del proveedor) se registra y devuelve False.
    """
    destino = (destino or "").strip()
    if "@" not in destino:
        print("[emailer] destino invalido, no se envia", flush=True)
        return False

    p = provider()
    if p == "none":
        # Modo local/tests: el enlace queda en el log para poder probar el flujo.
        print(f"[emailer] (none) para={destino} asunto={asunto!r}\n{texto or html}",
              flush=True)
        return False
    if not configurado():
        print(f"[emailer] proveedor {p!r} sin credencial/remitente: no se envia",
              flush=True)
        return False

    try:
        if p == "resend":
            return _enviar_resend(destino, asunto, html, texto)
        if p == "brevo":
            return _enviar_brevo(destino, asunto, html, texto)
        if p == "smtp":
            return _enviar_smtp(destino, asunto, html, texto)
        print(f"[emailer] proveedor desconocido {p!r}", flush=True)
        return False
    except Exception as exc:  # red, TLS, credenciales, cuota...
        print(f"[emailer] error enviando por {p}: {type(exc).__name__}: {exc}",
              flush=True)
        return False
