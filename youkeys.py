"""Normalizacion de API keys de You.com.

La API de You.com RECHAZA la key sin el prefijo 'ydc-' (401 Invalid or
expired API key). Si en el panel de hosting (Northflank) se pega la key tal
como sale en el JSON de configuracion (empieza por 'sk-'), el sitio deja de
funcionar. Este helper agrega el prefijo automaticamente para que funcione
con cualquiera de las dos formas.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _normalizar(key) -> str:
    if not key:
        return ""
    k = str(key).strip()
    if k.startswith("sk-") and not k.startswith("sk-ydc"):
        k = "ydc-" + k
    return k


def get_you_key() -> str:
    """YOU_API_KEY (o YDC_API_KEY) normalizada (Demian / answer)."""
    return _normalizar(os.getenv("YOU_API_KEY") or os.getenv("YDC_API_KEY"))


def get_you_search_key() -> str:
    """YOU_SEARCH_API_KEY (o YDC_API_KEY) normalizada (busqueda You.com)."""
    return _normalizar(
        os.getenv("YOU_SEARCH_API_KEY") or os.getenv("YOU_API_KEY")
        or os.getenv("YDC_API_KEY")
    )
