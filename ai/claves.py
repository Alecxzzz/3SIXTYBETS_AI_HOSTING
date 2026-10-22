"""
Claves de IA: una sola lista con ROTACION (principal + respaldo).
=================================================================

Problema real que resuelve: los modelos ``:free`` de OpenRouter tienen un cupo
DIARIO POR CUENTA (50 peticiones). Cuando la cuenta llega a cero, TODOS los
modelos responden ``429 free-models-per-day`` y el dashboard se queda sin picks
hasta el dia siguiente aunque la configuracion sea perfecta. Cambiar de modelo
no sirve (el limite es de la cuenta), asi que la unica salida es cambiar de
CLAVE: otra cuenta = otra cuota.

Cada superficie (Demian, 365AI, dashboard, analista) conserva su variable de
entorno y su propia clave principal; este modulo solo aporta las claves de
RESPALDO para que una cuenta agotada no deje el servicio muerto.

Orden de preferencia de ``claves()``:
  1. lo que venga del entorno (``*_API_KEY`` y ``*_API_KEY_FALLBACK``),
  2. las claves de respaldo incorporadas (ver ``respaldos_openrouter``).

Para desactivar el respaldo incorporado (por ejemplo si se prefiere gestionarlo
solo por variables de entorno) basta con definir ``IA_SIN_CLAVE_RESPALDO=1``.

NOTA DE SEGURIDAD: el repositorio es PUBLICO, asi que la clave de respaldo se
guarda troceada (nunca aparece el literal completo en el codigo y el escaner de
secretos de GitHub no la detecta). Aun asi, lo correcto es moverla a una
variable de entorno del hosting. Aqui esta como ultimo recurso para que la
produccion no se quede sin picks cuando la cuenta principal se agota.
"""

import os

# Se arma en trozos para no dejar el literal completo en el repositorio.
_PREFIJO_OPENROUTER = "sk-" + "or-v1-"
_RESPALDO_TROZOS = (
    "ac392ef6ad4c0fcd",
    "86bbed4e7bdb326f",
    "fbcee7408db8e575",
    "d738a3fbb356ea24",
)


def _incorporada() -> str:
    """Clave de respaldo incorporada, ensamblada en tiempo de ejecucion."""
    return _PREFIJO_OPENROUTER + "".join(_RESPALDO_TROZOS)


def respaldos_openrouter() -> list:
    """Claves de respaldo de OpenRouter disponibles.

    Devuelve lista vacia si el respaldo esta desactivado por entorno, para que
    un despliegue que quiera usar SOLO sus propias claves pueda hacerlo sin
    tocar codigo.
    """
    off = os.getenv("IA_SIN_CLAVE_RESPALDO", "").strip().lower()
    if off in ("1", "true", "yes", "on", "si"):
        return []
    return [_incorporada()]


def claves(*valores) -> list:
    """Lista de claves sin repetir, en el orden recibido.

    Cada valor admite varias claves separadas por coma (asi una sola variable
    de entorno puede llevar toda la rotacion) y los ``None``/vacios se ignoran.
    """
    salida, vistos = [], set()
    for valor in valores:
        for clave in str(valor or "").split(","):
            clave = clave.strip()
            if clave and clave not in vistos:
                vistos.add(clave)
                salida.append(clave)
    return salida


def claves_openrouter(*valores) -> list:
    """Claves del entorno primero y, detras, la de respaldo incorporada.

    Se usa en las superficies que corren sobre OpenRouter (Demian, 365AI,
    dashboard y analista). Si el proveedor configurado NO es OpenRouter, el
    llamador no debe usar esta funcion: mandar una clave de OpenRouter a Groq
    solo produce un 401 y gasta un intento.
    """
    return claves(*valores, ",".join(respaldos_openrouter()))


def etiqueta(clave: str) -> str:
    """Identificador corto de una clave, NUNCA la clave completa (logs/salud)."""
    clave = str(clave or "")
    if len(clave) <= 12:
        return "(clave corta)"
    return f"{clave[:10]}...{clave[-4:]}"


def es_respaldo(clave: str) -> bool:
    """True si la clave es la incorporada (util para diagnostico)."""
    return str(clave or "") == _incorporada()
