"""
Cliente OpenRouter (API compatible con OpenAI).

Un solo punto de salida para las IAs que corren sobre OpenRouter. Centraliza
lo que antes repetia cada modulo: headers obligatorios (HTTP-Referer /
X-Title), reintentos con espera, cambio de modelo cuando el proveedor responde
429/5xx y limpieza de los bloques de razonamiento que algunos modelos dejan
dentro del content.

Los modelos ":free" de OpenRouter tienen cupo DIARIO POR CUENTA (no por key),
por eso cada superficie usa su propia key: Demian, 365AI y dashboard no se
quitan cuota entre si.
"""

import os
import re
import time

import requests

try:  # claves con rotacion + deteccion unica de "cuota agotada"
    from ai.claves import claves_openrouter, etiqueta as _etiqueta_clave
    from ai.proveedor_fallos import sin_cuota as _sin_cuota
except ImportError:  # pragma: no cover - solo si se importa fuera del paquete
    def claves_openrouter(*valores):
        return [v.strip() for v in valores if v and str(v).strip()]

    def _etiqueta_clave(clave):
        return (clave or "")[:10] + "..."

    def _sin_cuota(texto):
        t = str(texto or "").lower()
        return "quota" in t or "rate limit" in t or "free-models-per-day" in t


try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

BASE_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "https://threesixtybets.com")
X_TITLE = os.getenv("OPENROUTER_X_TITLE", "3SIXTYBETS")
TIMEOUT = int(os.getenv("OPENROUTER_TIMEOUT", "150"))

# Modelos de razonamiento que exponen su cadena de pensamiento en el content.
_RE_RAZONAMIENTO = re.compile(
    r"<" + r"(?:think|thinking|reasoning)" + r">.*?</"
    + r"(?:think|thinking|reasoning)" + r">",
    re.IGNORECASE | re.DOTALL,
)

# Codigos en los que NO tiene sentido probar otro modelo: la credencial o la
# cuenta estan mal, no el modelo.
_CODIGOS_SIN_RELEVO = (401, 402, 403)


def limpiar(texto: str) -> str:
    """Quita bloques de razonamiento y espacios sobrantes del content."""
    texto = _RE_RAZONAMIENTO.sub("", texto or "")
    return texto.strip()


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter pide identificarse; sin esto algunas rutas dan 403.
        "HTTP-Referer": HTTP_REFERER,
        "X-Title": X_TITLE,
    }


def _payload(modelo, prompt_sistema, prompt_usuario, max_tokens, temperature, reasoning, tools):
    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": prompt_sistema or ""},
            {"role": "user", "content": prompt_usuario or ""},
        ],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    # El objeto "reasoning" es la forma estandar de OpenRouter. Los modelos que
    # no lo soportan lo ignoran; los de razonamiento lo usan para acotar el
    # presupuesto interno (sin esto el content puede salir vacio).
    if reasoning:
        payload["reasoning"] = (
            {"effort": reasoning} if isinstance(reasoning, str) else reasoning
        )
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


def generar(
    prompt_sistema: str,
    prompt_usuario: str,
    *,
    api_key: str | list[str],
    model: str,
    fallback: str | None = None,
    max_tokens: int = 1600,
    temperature: float | None = 0.7,
    reasoning="low",
    timeout: int | None = None,
    tools: list | None = None,
):
    """Llama a OpenRouter y devuelve (texto, modelo_usado) o (None, None).

    Estrategia:
      1. Intenta el modelo principal (2 pasadas en 429/5xx).
      2. Si el content sale vacio y el gasto fue en razonamiento, sube
         max_tokens una vez y reintenta.
      3. Si el modelo no existe o se agota, pasa al fallback.
      4. 401/402/403 cortan de inmediato: no es un problema del modelo.
    """
    # Rotacion de claves: la cuota de los modelos :free es POR CUENTA, asi que
    # cuando una clave responde 429 "free-models-per-day" se pasa a la
    # siguiente (otra cuenta = otra cuota) en vez de rendirse.
    claves_api = claves_openrouter(
        api_key if isinstance(api_key, str) else ",".join(api_key or [])
    )
    if not claves_api or not model:
        return None, None

    modelos = []
    for m in (model, fallback):
        if m and m not in modelos:
            modelos.append(m)

    timeout = timeout or TIMEOUT
    ultimo_error = ""

    for clave in claves_api:
        headers = _headers(clave)
        clave_agotada = False
        for modelo in modelos:
            payload = _payload(
                modelo, prompt_sistema, prompt_usuario,
                max_tokens, temperature, reasoning, tools,
            )
            for intento in range(2):
                try:
                    response = requests.post(
                        BASE_URL,
                        headers=headers,
                        json=payload,
                        timeout=timeout,
                    )
                except requests.exceptions.RequestException as exc:
                    ultimo_error = f"red: {exc}"
                    time.sleep(3)
                    continue

                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        ultimo_error = f"{modelo}: respuesta no JSON"
                        break

                    opciones = data.get("choices") or []
                    mensaje = (opciones[0].get("message") if opciones else {}) or {}
                    texto = limpiar(mensaje.get("content") or "")
                    if texto:
                        return texto, data.get("model") or modelo

                    # Content vacio: el razonamiento consumio el presupuesto.
                    detalles = (
                        (data.get("usage") or {}).get("completion_tokens_details") or {}
                    )
                    if detalles.get("reasoning_tokens") and payload["max_tokens"] < 4096:
                        payload["max_tokens"] = min(4096, int(payload["max_tokens"]) * 2)
                        continue

                    ultimo_error = f"{modelo}: contenido vacio"
                    break

                try:
                    detalle = response.json()
                    mensaje_error = (
                        detalle.get("error", {}).get("message") or response.text[:200]
                    )
                except ValueError:
                    mensaje_error = (response.text or "")[:200]
                ultimo_error = f"{modelo} {response.status_code}: {mensaje_error}"

                # Cuota de la CUENTA agotada (401/402/403 o 429 de cupo diario):
                # el modelo no tiene la culpa, la clave si. Se corta aqui y se
                # pasa a la siguiente clave SIN dormir.
                if response.status_code in _CODIGOS_SIN_RELEVO or (
                    response.status_code == 429 and _sin_cuota(ultimo_error)
                ):
                    print(
                        f"[OpenRouter] clave {_etiqueta_clave(clave)} sin cuota: "
                        f"{ultimo_error}",
                        flush=True,
                    )
                    clave_agotada = True
                    break

                if response.status_code in (429, 500, 502, 503, 504):
                    time.sleep(3 if intento == 0 else 8)
                    continue

                # 400/404: modelo invalido o retirado -> probar el fallback.
                break
            if clave_agotada:
                break
        if not clave_agotada:
            # Se recorrieron todos los modelos con esta clave sin senal de
            # cuota: no hay otra clave que pueda arreglarlo.
            break

    print(f"[OpenRouter] sin respuesta: {ultimo_error}", flush=True)
    return None, None
