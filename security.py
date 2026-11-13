# -*- coding: utf-8 -*-
"""SEGURIDAD CENTRALIZADA del backend.

Por que existe un modulo aparte
-------------------------------
La seguridad estaba repartida: `_ip_del_cliente` en main.py, el hash de
sesiones implicito en db.py, las cabeceras en un middleware, el rate limit en
un dict global. Cada endpoint nuevo podia (y puede) olvidarse de uno de los
tres. Aqui viven las primitivas y los endpoints solo las llaman.

Que cubre
---------
  ip_del_cliente()   IP REAL resistente a X-Forwarded-For falsificado
  limite()           rate limit por ventana deslizante
  bloqueado()        bloqueo por CUENTA tras N fallos (frena fuerza bruta
                     distribuida, que el limite por IP no ve)
  evento()           registro de auditoria (quien, que, desde donde, si salio bien)
  hash_token()       los tokens de sesion NO se guardan en claro
  validar_password() politica minima de contrasena
  csp()              Content-Security-Policy que no rompe los reproductores

Que NO cubre (es infraestructura, no codigo): WAF, backups, 2FA, rotacion
automatica de secretos. Ver SEGURIDAD.md.
"""

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
from typing import Optional, Tuple

from fastapi import HTTPException, Request

# --------------------------------------------------------------------------
# CONFIGURACION
# --------------------------------------------------------------------------
# Saltos de proxy DE CONFIANZA delante del backend. Railway/Render ponen UNO
# (su edge). Si algun dia se mete Cloudflare delante son DOS: si se queda en 1,
# se leeria la IP de Cloudflare y todos los clientes compartirian limite.
PROXY_HOPS = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
# Cloudflare manda la IP real en su propia cabecera, que el cliente NO puede
# falsificar porque la sobrescribe el edge.
CONFIAR_CLOUDFLARE = os.getenv("TRUST_CLOUDFLARE", "").strip().lower() in (
    "1", "true", "yes"
)

# Bloqueo por cuenta: tras N fallos, la cuenta queda bloqueada BLOQUEO_MIN
# minutos aunque el atacante cambie de IP.
FALLOS_ANTES_DE_BLOQUEO = int(os.getenv("SEC_FALLOS_BLOQUEO", "8"))
BLOQUEO_MIN = int(os.getenv("SEC_BLOQUEO_MIN", "15"))

_lock = threading.Lock()
_bucket: dict = {}
_fallos: dict = {}      # usuario -> [timestamps de fallo]
_bloqueos: dict = {}    # usuario -> hasta (epoch)


# --------------------------------------------------------------------------
# IP REAL
# --------------------------------------------------------------------------
def ip_del_cliente(request: Request) -> str:
    """IP del cliente que NO se puede falsificar desde fuera.

    El problema que resuelve: el codigo anterior hacia `xff.split(",")[0]`, o
    sea se quedaba con el PRIMER valor de X-Forwarded-For. Pero el primero es
    justo el que manda el atacante: los proxies AÑADEN su salto al final, no al
    principio. Con `split(",")[0]` bastaba

        curl -H "X-Forwarded-For: 1.2.3.4" ...

    para obtener una IP nueva por peticion y saltarse TODOS los limites (login,
    signup, chat, FFmpeg). Aqui se toma el salto que puso el proxy de confianza
    contando desde el final, y se valida que sea una IP de verdad.
    """
    if CONFIAR_CLOUDFLARE:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if _ip_valida(cf):
            return cf

    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        saltos = [s.strip() for s in xff.split(",") if s.strip()]
        # Los ULTIMOS PROXY_HOPS valores los escribio la infraestructura; lo que
        # haya antes lo pudo inventar el cliente.
        if len(saltos) >= PROXY_HOPS:
            candidata = saltos[-PROXY_HOPS]
            if _ip_valida(candidata):
                return candidata
        for salto in reversed(saltos):
            if _ip_valida(salto):
                return salto

    host = getattr(request.client, "host", None) if request.client else None
    return host if _ip_valida(host or "") else "desconocida"


def _ip_valida(valor: str) -> bool:
    """True si es una IPv4/IPv6 parseable. Evita que basura contamine el rate
    limit (rompiendo el diccionario o colisionando claves)."""
    if not valor:
        return False
    try:
        ipaddress.ip_address(valor.strip())
        return True
    except ValueError:
        return False


def es_ip_privada(valor: str) -> bool:
    try:
        return ipaddress.ip_address((valor or "").strip()).is_private
    except ValueError:
        return True


# --------------------------------------------------------------------------
# LIMITES Y BLOQUEOS
# --------------------------------------------------------------------------
def limite(clave: str, max_eventos: int, ventana_s: int) -> None:
    """Ventana deslizante por clave. Lanza 429 al exceder."""
    ahora = time.time()
    with _lock:
        eventos = [t for t in _bucket.get(clave, []) if ahora - t < ventana_s]
        if len(eventos) >= max_eventos:
            _bucket[clave] = eventos
            raise HTTPException(
                429, "Demasiadas peticiones. Espera unos minutos e intenta de nuevo."
            )
        eventos.append(ahora)
        _bucket[clave] = eventos
        if len(_bucket) > 20000:   # poda barata para no crecer sin limite
            for k in [k for k, v in _bucket.items()
                      if not v or ahora - v[-1] > ventana_s]:
                _bucket.pop(k, None)


def bloqueado(usuario: str) -> bool:
    """True si la cuenta esta en bloqueo temporal por fallos de login."""
    if not usuario:
        return False
    ahora = time.time()
    with _lock:
        hasta = _bloqueos.get(usuario.lower())
        if hasta and hasta > ahora:
            return True
        if hasta:
            _bloqueos.pop(usuario.lower(), None)
    return False


def registrar_fallo(usuario: str) -> None:
    """Cuenta un fallo de login y bloquea la cuenta al llegar al umbral.

    El bloqueo es POR CUENTA, no por IP: un atacante con mil IPs sigue
    pegandole a la misma cuenta y se bloquea igual.
    """
    if not usuario:
        return
    clave = usuario.lower()
    ahora = time.time()
    with _lock:
        fallos = [t for t in _fallos.get(clave, []) if ahora - t < 3600]
        fallos.append(ahora)
        _fallos[clave] = fallos
        if len(fallos) >= FALLOS_ANTES_DE_BLOQUEO:
            _bloqueos[clave] = ahora + BLOQUEO_MIN * 60
            _fallos.pop(clave, None)


def limpiar_fallos(usuario: str) -> None:
    if not usuario:
        return
    with _lock:
        _fallos.pop(usuario.lower(), None)
        _bloqueos.pop(usuario.lower(), None)


# --------------------------------------------------------------------------
# USUARIO DE UN TOKEN (con cache corto)
# --------------------------------------------------------------------------
# Los segmentos de video se piden ~1 cada 4 s por espectador. Consultar la BD en
# cada uno es una query por segmento y por persona: con 100 espectadores son 25
# consultas por segundo solo para autenticar video. Con 60 s de cache baja a
# casi cero y el peaje es que una sesion revocada sigue viva como maximo un
# minuto (lo mismo que acepta cualquier CDN).
USUARIO_TTL_S = int(os.getenv("SEC_USUARIO_TTL", "60"))
_usuarios: dict = {}


def usuario_de_token(token: str):
    """Usuario dueño del token, cacheado USUARIO_TTL_S segundos. None si no vale."""
    if not token:
        return None
    ahora = time.time()
    with _lock:
        guardado = _usuarios.get(token)
        if guardado and ahora - guardado[0] < USUARIO_TTL_S:
            return guardado[1]
    try:
        import db  # import local para no acoplar el arranque

        user = db.get_user_by_token(token)
    except Exception:  # noqa: BLE001
        return None
    with _lock:
        _usuarios[token] = (ahora, user)
        if len(_usuarios) > 5000:   # poda: se tiran los vencidos
            for k in [k for k, v in _usuarios.items()
                      if ahora - v[0] > USUARIO_TTL_S]:
                _usuarios.pop(k, None)
    return user


def olvidar_usuario(token: str) -> None:
    """Invalida la cache al cerrar sesion (no esperar al TTL)."""
    if not token:
        return
    with _lock:
        _usuarios.pop(token, None)



# --------------------------------------------------------------------------
# TOKENS Y CONTRASENAS
# --------------------------------------------------------------------------
def token_nuevo() -> str:
    """Token de sesion. 32 bytes urlsafe = 256 bits de entropia."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash del token para guardarlo en BD.

    Un dump de `sessions` con los tokens en claro regala todas las sesiones
    activas. Con el hash no sirve de nada: el token solo lo tiene el navegador.
    No se usa bcrypt a proposito: el token ya tiene 256 bits de entropia, no es
    una contrasena adivinable, y sha256 no añade latencia a cada peticion.
    """
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def iguales_seguro(a: str, b: str) -> bool:
    """Comparacion en tiempo constante (no filtra informacion por temporizacion)."""
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


COMUNES = {
    "123456", "12345678", "123456789", "1234567890", "password", "password1",
    "password123", "contrasena", "qwerty", "admin", "admin123", "iloveyou",
    "welcome", "abc123", "futbol", "apuestas", "bets", "3sixty", "3sixtybets",
    "soccer", "deportes", "bienvenido", "temporal",
}


def validar_password(password: str) -> Tuple[bool, str]:
    """Politica minima. Devuelve (ok, motivo).

    12 caracteres, letras Y numeros, y no una de las mas usadas. Sin esto el
    hash PBKDF2 protege poco: a una contrasena debil se le ataca con
    diccionario, no con fuerza bruta.
    """
    pwd = password or ""
    if len(pwd) < 12:
        return False, "La contrasena debe tener al menos 12 caracteres."
    if len(pwd) > 200:
        return False, "La contrasena es demasiado larga."
    if pwd.lower() in COMUNES:
        return False, "Esa contrasena es demasiado comun. Elige otra."
    if not re.search(r"[A-Za-z]", pwd) or not re.search(r"\d", pwd):
        return False, "La contrasena debe mezclar letras y numeros."
    return True, ""


# --------------------------------------------------------------------------
# AUDITORIA
# --------------------------------------------------------------------------
def evento(tipo: str, resultado: str = "ok", usuario: Optional[str] = None,
           request: Optional[Request] = None, detalle: Optional[str] = None) -> None:
    """Registra un evento de seguridad. NUNCA lanza: auditar no puede romper
    la operacion que se estaba auditando.

    Que se guarda y por que: `login_fallido`, `login_ok`, `bloqueo`, `signup`,
    `admin_accion`, `acceso_denegado`. Sin este rastro, ante un incidente no se
    puede responder ni "que paso" ni "que se llevaron". No se guarda la
    contrasena ni el token, jamas.
    """
    try:
        import db  # import local: security no debe depender de la BD al cargar

        ip = ip_del_cliente(request) if request is not None else None
        ua = ""
        if request is not None:
            ua = (request.headers.get("user-agent") or "")[:200]
        db.registrar_evento_seguridad(
            tipo=tipo, resultado=resultado, usuario=usuario, ip=ip,
            user_agent=ua, detalle=(detalle or "")[:300],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[seguridad] no se pudo auditar {tipo}: {exc}", flush=True)


# --------------------------------------------------------------------------
# SESION: COOKIE HttpOnly (ademas del header Bearer)
# --------------------------------------------------------------------------
# Caducidad de la cookie = SESION_DIAS de db.py (30). Se repite aqui para no
# importar db en el arranque.
DIAS_COOKIE = int(os.getenv("SEC_COOKIE_DIAS", "30"))
COOKIE_NOMBRE = os.getenv("SEC_COOKIE_NAME", "sb_session")
# 'none' es lo que exige un frontend en OTRO dominio (Vercel -> Railway).
# Cuando el frontend pase por un rewrite same-origin (/api/* -> backend), se
# debe poner SEC_COOKIE_SAMESITE=strict: es la proteccion CSRF real.
SAMESITE = (os.getenv("SEC_COOKIE_SAMESITE", "none") or "none").strip().lower()


def _es_https(request: Request) -> bool:
    return (request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https")


def poner_cookie_sesion(respuesta, token: str, request: Request) -> None:
    """Marca la cookie de sesion: HttpOnly + Secure + SameSite.

    Por que importa: hoy el token vive en localStorage y lo lee cualquier
    JavaScript de la pagina. Con HttpOnly, un XSS NO puede robarlo. Se mantiene
    el header Authorization (Bearer) en paralelo para no romper el frontend
    actual: la cookie es el camino seguro, el Bearer es la compatibilidad.
    """
    try:
        respuesta.set_cookie(
            key=COOKIE_NOMBRE,
            value=token,
            max_age=DIAS_COOKIE * 86400,
            httponly=True,                      # inaccesible para JavaScript
            secure=_es_https(request),          # solo por HTTPS
            samesite=SAMESITE,                  # 'none' cross-site / 'strict' same-origin
            path="/",
        )
    except Exception as exc:  # noqa: BLE001 - si falla, el Bearer sigue valiendo
        print(f"[seguridad] no se pudo poner la cookie: {exc}", flush=True)


def borrar_cookie_sesion(respuesta) -> None:
    try:
        respuesta.delete_cookie(COOKIE_NOMBRE, path="/")
    except Exception:  # noqa: BLE001
        pass


def token_de_request(request: Request) -> str:
    """Token de sesion: primero la cookie HttpOnly, luego el header Bearer."""
    cookie = ""
    try:
        cookie = request.cookies.get(COOKIE_NOMBRE) or ""
    except Exception:  # noqa: BLE001
        cookie = ""
    if cookie:
        return cookie
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


# --------------------------------------------------------------------------
# CABECERAS
# --------------------------------------------------------------------------
# La CSP se aplica SOLO a HTML: en JSON no aporta nada y en los segmentos HLS
# podria interferir con los reproductores.
#
# 'unsafe-inline' en script-src es necesario porque las paginas que sirve el
# backend (/perfil, /recuperar, track_page) llevan JavaScript en linea. No es
# ideal, pero sigue bloqueando el vector principal: cargar un script de OTRO
# dominio si alguien inyecta HTML. Migrar esos scripts a archivos propios
# permitiria quitarlo (ver SEGURIDAD.md).
CSP_HTML = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "media-src * blob: data:; "
    "connect-src 'self' https:; "
    "font-src 'self' data:; "
    "frame-src 'self' https:; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


def aplicar_cabeceras(respuesta, request: Request) -> None:
    """Cabeceras de seguridad. Idempotente: no pisa lo que ya puso el endpoint."""
    cabeceras = respuesta.headers
    cabeceras.setdefault("X-Content-Type-Options", "nosniff")
    cabeceras.setdefault("X-Frame-Options", "SAMEORIGIN")
    cabeceras.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    cabeceras.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
    )
    cabeceras.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # HSTS solo por HTTPS: en local por HTTP rompe el desarrollo.
    if _es_https(request):
        cabeceras.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
        )
    tipo = (cabeceras.get("content-type") or "").lower()
    if tipo.startswith("text/html"):
        cabeceras.setdefault("Content-Security-Policy", CSP_HTML)
        # Las paginas del backend llevan datos del usuario: nunca cachear.
        cabeceras.setdefault("Cache-Control", "no-store")


__all__ = [
    "CONFIAR_CLOUDFLARE", "COOKIE_NOMBRE", "PROXY_HOPS", "SAMESITE",
    "aplicar_cabeceras", "bloqueado", "borrar_cookie_sesion", "es_ip_privada",
    "evento", "hash_token", "iguales_seguro", "ip_del_cliente", "limite",
    "limpiar_fallos", "poner_cookie_sesion", "registrar_fallo", "token_de_request",
    "token_nuevo", "validar_password",
]



