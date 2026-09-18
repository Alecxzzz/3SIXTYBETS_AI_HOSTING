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
        """Busqueda de contexto en You.com (ydc-index)."""
        if not self.you_api_key:
            return []

        try:
            querystring = {
                "query": consulta,
                "count": str(cantidad),
                "freshness": "day",
                "language": "ES",
                "safesearch": "off",
                "crawl_timeout": "10",
            }
            headers = {
                "X-API-KEY": self.you_api_key,
                "Accept": "application/json",
            }
            response = requests.get(
                self.you_search_url,
                headers=headers,
                params=querystring,
                timeout=15,
            )
            data = response.json()
        except Exception:
            return []

        datos = []
        for item in data.get("hits", [])[:cantidad]:
            datos.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "body": item.get("snippet") or item.get("description") or "",
                "source": "you",
            })

        return datos

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

    def answer_full(self, query, system_prompt="", research_effort="deep",
                    include_domains=None, freshness="day"):
        """Igual que answer(), pero devuelve el dict COMPLETO de /v1/answer.

        Estructura real de la API:
        {"answer": "...", "citations": [{"source": url, "excerpts": [...]}],
         "results": {"web": [{"url", "title", "snippets"}]}}
        Nota: segun el plan, 'research_effort' y 'extraction' pueden venir
        rechazados (422 extra_forbidden); en ese caso se reintentan sin ellos.
        """
        api_key = youkeys.get_you_key() or youkeys.get_you_search_key()
        if not api_key:
            return {"answer": "ERROR: Falta la API key para You.com en el backend."}

        consulta = f"{system_prompt}\n\n{query}".strip() if system_prompt else query
        # /v1/answer limita 'query' a 400 caracteres: no cabe el prompt largo
        # de sistema. Se comprime el system prompt (una linea), se recorta y
        # la pregunta del usuario tiene prioridad sobre el prefijo.
        LIMITE_QUERY = 400
        SUFIJO_ES = " Responde SIEMPRE en espanol."
        pregunta = query.strip()
        prefijo = ""
        if system_prompt:
            comprimido = " ".join(system_prompt.split())
            espacio = LIMITE_QUERY - len(pregunta) - len(SUFIJO_ES) - 4
            if espacio > 60:
                prefijo = comprimido[:espacio]
                corte = prefijo.rfind(" ")
                if corte > 40:
                    prefijo = prefijo[:corte]
                prefijo += "..."
        total = len(prefijo) + len(pregunta) + len(SUFIJO_ES) + (4 if prefijo else 0)
        if total > LIMITE_QUERY:
            # la pregunta manda: recortar el prefijo (o quitarlo si no cabe)
            exceso = total - LIMITE_QUERY
            if prefijo and exceso < len(prefijo) - 60:
                prefijo = prefijo[:-exceso].rsplit(" ", 1)[0] + "..."
            else:
                prefijo = ""
                pregunta = pregunta[: LIMITE_QUERY - len(SUFIJO_ES)]
        consulta = (f"{prefijo}\n\n{pregunta}" if prefijo else pregunta) + SUFIJO_ES
        consulta = consulta[:LIMITE_QUERY]

        effort = normalizar_research_effort(
            research_effort or os.getenv("YOU_RESEARCH_EFFORT", "deep")
        )

        payload = {
            "query": consulta,
            "freshness": os.getenv("YOU_FRESHNESS", freshness),
            "safesearch": os.getenv("YOU_SAFESEARCH", "strict"),
            "language": os.getenv("YOU_LANGUAGE", "ES"),
        }
        # research_effort y extraction solo se envian si el plan los soporta:
        # se intentan una vez y, si la API los rechaza (422 extra_forbidden),
        # se retiran automaticamente (ver bucle de abajo).
        if os.getenv("YOU_SEND_EFFORT", "1") not in ("0", "false", "False"):
            payload["research_effort"] = effort
        payload["extraction"] = {
            "extraction_mode": "full_page",
            "extraction_source": "fetch",
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

        # La API rechaza con 422 los campos que no soporta. Se reintenta
        # quitando los campos rechazados hasta 3 veces.
        for _ in range(3):
            try:
                response = requests.post(
                    YOU_ANSWER_URL, headers=headers, json=payload, timeout=60
                )
            except Exception as error:
                return {"answer": f"Error leyendo respuesta de You.com: {error}"}
            if response.ok:
                try:
                    return response.json()
                except Exception:
                    return {"answer": response.text}
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
            return {
                "answer": (
                    f"Error de You.com ({response.status_code}): "
                    f"{response.text[:800]}"
                )
            }
        return {"answer": "Error de You.com: no se pudo obtener respuesta tras los reintentos."}

    def answer(self, query, system_prompt="", research_effort="deep",
               include_domains=None, freshness="day"):
        """Texto de respuesta de /v1/answer (lo usan TODOS los puntos del sitio)."""
        data = self.answer_full(
            query,
            system_prompt=system_prompt,
            research_effort=research_effort,
            include_domains=include_domains,
            freshness=freshness,
        )
        return _limpiar_citas(_extraer_texto_you(data))

    def ask_you(self, question, system_prompt="", research_effort="standard"):
        """Flujo PRINCIPAL de You.com: /v1/research (razonamiento + web).

        1) Contexto web reciente via ydc-index (busqueda de ultimos dias).
        2) POST /v1/research con 'input' (hasta 39,000 chars) y TODOS los
           parametros pedidos: freshness day, safesearch strict, idioma ES,
           include_domains (sofascore.com + flashscore.com) y extraccion
           full_page/fetch. Si la API rechaza algun campo (422), se reintenta
           sin el campo rechazado.
        """
        research_effort = normalizar_research_effort(research_effort)
        api_key = youkeys.get_you_key()
        if not api_key:
            return "ERROR: Falta la API key para You.com en el backend."

        # research_effort="frontier" SIEMPRE requiere background=true.
        # Este backend no implementa polling (GET /v1/research/{task_id}),
        # asi que si llega "frontier" lo bajamos a "exhaustive" para evitar
        # un 422 garantizado en cada llamada sincrona.
        if research_effort == "frontier":
            research_effort = "exhaustive"

        try:
            ydc_url = os.getenv("YOU_SEARCH_URL", "https://ydc-index.io/v1/search")
            querystring = {
                "query": question,
                "count": "5",
                "freshness": "day",
                "language": "ES",
                "safesearch": "off",
                "crawl_timeout": "10",
            }
            headers_ydc = {
                "X-API-KEY": api_key,
                "Accept": "application/json",
            }
            ydc_response = requests.get(ydc_url, headers=headers_ydc, params=querystring, timeout=15)
            ydc_data = ydc_response.json()

            context = ""
            for item in ydc_data.get("hits", [])[:5]:
                title = item.get("title", "")
                snippet = item.get("snippet") or item.get("description") or ""
                # Recortamos cada snippet para que 5 resultados nunca puedan
                # inflar el prompt hasta el limite de 40,000 caracteres de "input".
                snippet = snippet[:600]
                context += f"{title}\n{snippet}\n\n"
        except Exception:
            context = "No se pudo obtener contexto externo."

        url = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }
        full_prompt = f"""
{system_prompt}

Informacion reciente encontrada:
{context}

Analiza este evento deportivo y responde en espanol:
{question}
"""

        # Limite duro documentado por You.com para "input": 40,000 caracteres.
        LIMITE_INPUT = 39000
        if len(full_prompt) > LIMITE_INPUT:
            full_prompt = full_prompt[:LIMITE_INPUT].rstrip() + "\n...(recortado por limite de caracteres de You.com)"

        payload = {
            "input": full_prompt,
            "research_effort": research_effort,
            "background": False,
            "freshness": os.getenv("YOU_FRESHNESS", "day"),
            "safesearch": os.getenv("YOU_SAFESEARCH", "strict"),
            "language": os.getenv("YOU_LANGUAGE", "ES"),
            "extraction": {
                "extraction_mode": "full_page",
                "extraction_source": "fetch",
            },
        }
        dominios = _you_include_domains()
        if dominios:
            payload["include_domains"] = dominios

        # Tolerante a cambios del API: si /v1/research rechaza algun campo
        # extra con 422, se reintenta sin el campo rechazado.
        respuesta = None
        for _ in range(4):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=90
                )
            except Exception as error:
                return f"Error leyendo respuesta de You.com: {error}"
            if response.ok:
                respuesta = response.json()
                break
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
            try:
                detalle = response.json()
            except Exception:
                detalle = response.text[:800]
            return f"Error de You.com ({response.status_code}): {detalle}"

        if respuesta is None:
            return "Error de You.com: no se pudo obtener respuesta tras los reintentos."

        if isinstance(respuesta, dict):
            if "output" in respuesta and isinstance(respuesta["output"], dict) and "content" in respuesta["output"]:
                return _limpiar_citas(respuesta["output"]["content"])
            for key in ("answer", "content", "text", "result"):
                if key in respuesta and isinstance(respuesta[key], str):
                    return _limpiar_citas(respuesta[key])
            if "output" in respuesta and isinstance(respuesta["output"], str):
                return _limpiar_citas(respuesta["output"])

        return str(respuesta)

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