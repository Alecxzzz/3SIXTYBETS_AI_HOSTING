import json
import os
import re

import requests

import youkeys

# Marcas de citacion de You.com: [[1]], [[1, 2]], 【3】... (SIEMPRE doble
# corchete o 【】; el regex NO toca arrays JSON como [1, 2] ni ["a", "b"]).
_RE_CITAS = re.compile(r"\[\[\s*[\d,\s|]{1,12}\]\]\s*|【\s*[\d,\s|]{1,12}】\s*")

# API de respuestas de You.com (Smart/Answer): UN solo endpoint para busqueda
# y respuestas con investigacion web. Se usa en TODOS los puntos del sitio
# (chat, dashboard/picks, estadisticas y verificacion de aciertos).
YOU_ANSWER_URL = os.getenv("YOU_ANSWER_URL", "https://api.you.com/v1/answer")


def _you_include_domains():
    """Dominios a los que se limita la busqueda de You.com (Sofascore primero)."""
    raw = os.getenv("YOU_INCLUDE_DOMAINS", "sofascore.com,flashscore.com")
    return [d.strip() for d in raw.split(",") if d.strip()]


def _extraer_texto_you(data):
    """Extrae el texto de respuesta del JSON de /v1/answer (tolerante)."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return str(data)
    if isinstance(data.get("output"), dict) and "content" in data["output"]:
        return data["output"]["content"]
    for key in ("answer", "content", "text", "result"):
        if isinstance(data.get(key), str):
            return data[key]
    if isinstance(data.get("output"), str):
        return data["output"]
    return str(data)


def _limpiar_citas(texto: str) -> str:
    if not texto:
        return texto
    # Si el texto es JSON (respuestas del pipeline de picks), NO tocarlo:
    # un regex podria comerse arrays numericos como [1, 2]. Los campos se
    # limpian campo por campo con _limpiar_artefactos en dashboard.
    strip = texto.strip()
    if strip.startswith("{") or strip.startswith("["):
        try:
            json.loads(strip)
            return strip
        except ValueError:
            pass
    limpio = _RE_CITAS.sub("", texto)
    return re.sub(r"[ \t]{2,}", " ", limpio).strip()

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - depends on environment packages
    DDGS = None


VALID_YOU_RESEARCH_EFFORTS = {"lite", "standard", "deep", "exhaustive", "frontier"}


def normalizar_research_effort(value):
    if value is None:
        return "standard"

    v = str(value).strip().lower()
    if not v:
        return "standard"

    aliases = {
        "medium": "standard",
        "normal": "standard",
        "default": "standard",
        "quick": "lite",
        "low": "lite",
        "fast": "lite",
        "high": "deep",
        "max": "exhaustive",
        "heavy": "deep",
        "very_deep": "deep",
    }

    if v in VALID_YOU_RESEARCH_EFFORTS:
        return v

    return aliases.get(v, "standard")


class SearchEngine:
    def __init__(self):
        self.ddgs = DDGS() if DDGS is not None else None
        self.you_api_key = youkeys.get_you_search_key()
        self.you_search_url = os.getenv("YOU_SEARCH_URL", "https://ydc-index.io/v1/search")

    def buscar_you(self, consulta, cantidad=4):
        """Busqueda de contexto via /v1/answer (You.com Smart).

        La nueva API devuelve una RESPUESTA investigada (no una lista de
        hits); se envuelve como un unico 'resultado' para que los
        consumidores (main.buscar_web, contexto del chat) sigan funcionando.
        """
        texto = self.answer(consulta)
        if not texto or texto.startswith(("ERROR", "Error de You.com", "Error leyendo")):
            return []
        return [{
            "title": "You.com (analisis en vivo)",
            "url": YOU_ANSWER_URL,
            "body": texto[:2000],
            "source": "you",
        }]

    def buscar_ddgs(self, consulta, cantidad=4):
        datos = []

        if self.ddgs is None:
            return datos

        try:
            for r in self.ddgs.text(consulta, max_results=cantidad):
                datos.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "body": r.get("body", ""),
                    "source": "ddgs",
                })
        except Exception:
            pass

        return datos

    def buscar(self, consulta, cantidad=4, proveedor="ddgs"):
        if proveedor == "you":
            resultados = self.buscar_you(consulta, cantidad)
            if resultados:
                return resultados

        return self.buscar_ddgs(consulta, cantidad)

    def answer(self, query, system_prompt="", research_effort="deep",
               include_domains=None, freshness="day"):
        """Consulta UNICA a You.com Smart/Answer (api.you.com/v1/answer).

        Payload exacto pedido por el usuario: freshness day, research_effort
        deep, extraccion full_page/fetch, safesearch strict, idioma ES y
        include_domains limitado a sofascore.com + flashscore.com.
        Todos los puntos del sitio (chat, dashboard, estadisticas y
        verificacion de aciertos) pasan por aqui.
        """
        api_key = youkeys.get_you_key() or youkeys.get_you_search_key()
        if not api_key:
            return "ERROR: Falta la API key para You.com en el backend."

        consulta = f"{system_prompt}\n\n{query}".strip() if system_prompt else query
        # Limite duro de caracteres del prompt
        LIMITE_INPUT = 39000
        if len(consulta) > LIMITE_INPUT:
            consulta = consulta[:LIMITE_INPUT].rstrip()

        effort = normalizar_research_effort(
            research_effort or os.getenv("YOU_RESEARCH_EFFORT", "deep")
        )

        payload = {
            "query": consulta,
            "freshness": os.getenv("YOU_FRESHNESS", freshness),
            "research_effort": effort,
            "extraction": {
                "extraction_mode": "full_page",
                "extraction_source": "fetch",
            },
            "safesearch": os.getenv("YOU_SAFESEARCH", "strict"),
            "language": os.getenv("YOU_LANGUAGE", "ES"),
        }
        dominios = (
            include_domains if include_domains is not None
            else _you_include_domains()
        )
        if dominios:
            payload["include_domains"] = dominios

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

        # La API rechaza con 422 los campos que no soporta (ej. research_effort
        # o extraction segun el plan). Se reintenta quitando los campos
        # rechazados hasta 3 veces para ser tolerante a cambios del API.
        for _ in range(3):
            try:
                response = requests.post(
                    YOU_ANSWER_URL, headers=headers, json=payload, timeout=60
                )
            except Exception as error:
                return f"Error leyendo respuesta de You.com: {error}"
            if response.ok:
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return _limpiar_citas(_extraer_texto_you(data))
            if response.status_code == 422:
                try:
                    detalle = response.json()
                except Exception:
                    detalle = {}
                campos = set()
                for d in (detalle.get("detail") or []) if isinstance(detalle, dict) else []:
                    loc = d.get("loc") or []
                    if d.get("type") == "extra_forbidden" and len(loc) > 1:
                        campos.add(loc[1])
                if campos and any(c in payload for c in campos):
                    for c in campos:
                        payload.pop(c, None)
                    continue
            return (
                f"Error de You.com ({response.status_code}): "
                f"{response.text[:800]}"
            )
        return "Error de You.com: no se pudo obtener respuesta tras los reintentos."

    def ask_you(self, question, system_prompt="", research_effort="deep"):
        """Compatibilidad: todo You.com pasa por /v1/answer."""
        return self.answer(
            question,
            system_prompt=system_prompt,
            research_effort=research_effort,
        )

    def buscar_varias(self, consultas, proveedor="ddgs"):
        resultados = []

        for consulta in consultas:
            resultados.extend(self.buscar(consulta, proveedor=proveedor))

        return resultados

    def score(self, resultados):
        score = {
            "stats": 0,
            "odds": 0,
            "injuries": 0,
            "h2h": 0,
            "lineups": 0,
            "preview": 0,
        }

        for r in resultados:
            texto = (r["title"] + " " + r["body"]).lower()

            if "odds" in texto or "bet" in texto:
                score["odds"] += 1

            if "injur" in texto:
                score["injuries"] += 1

            if "lineup" in texto:
                score["lineups"] += 1

            if "head to head" in texto or "h2h" in texto:
                score["h2h"] += 1

            if "preview" in texto:
                score["preview"] += 1

            if "stats" in texto or "average" in texto or "last" in texto:
                score["stats"] += 1

        return score

    def suficiente(self, score):
        total = 0

        for valor in score.values():
            if valor > 0:
                total += 1

        return total >= 4

    def recopilar(self, partido, proveedor="ddgs"):
        consultas = [
            partido,
            partido + " odds, cuotas",
            partido + " injuries, lesiones",
            partido + " stats",
            partido + " last games",
            partido + " h2h",
        ]

        resultados = self.buscar_varias(consultas, proveedor=proveedor)
        score = self.score(resultados)

        if not self.suficiente(score):
            consultas2 = [
                partido + " stake.com odds",
                partido + " probable lineup sofascore",
                partido + " flashscore",
                partido + " sofascore",
                partido + " statmuse",
                partido + " injuries espn",
            ]

            resultados.extend(self.buscar_varias(consultas2, proveedor=proveedor))

        return resultados