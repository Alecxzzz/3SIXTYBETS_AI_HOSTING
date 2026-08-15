import os

import requests

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
        self.you_api_key = os.getenv("YOU_SEARCH_API_KEY") or os.getenv("YOU_API_KEY")
        self.you_search_url = os.getenv("YOU_SEARCH_URL", "https://ydc-index.io/v1/search")

    def buscar_you(self, consulta, cantidad=4):
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

    def ask_you(self, question, system_prompt="", research_effort="standard"):
        research_effort = normalizar_research_effort(research_effort)
        api_key = os.getenv("YOU_API_KEY") or os.getenv("YOU_SEARCH_API_KEY")
        if not api_key:
            return "ERROR: Falta la API key para You.com en el backend."

        # research_effort="frontier" SIEMPRE requiere background=true.
        # Este backend no implementa polling (GET /v1/research/{task_id}),
        # así que si llega "frontier" lo bajamos a "exhaustive" para evitar
        # un 422 garantizado en cada llamada síncrona.
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
                # inflar el prompt hasta el límite de 40,000 caracteres de "input".
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

Información reciente encontrada:
{context}

Analiza este evento deportivo:
{question}
"""

        # Límite duro documentado por You.com para "input": 40,000 caracteres.
        # Dejamos margen de seguridad.
        LIMITE_INPUT = 39000
        if len(full_prompt) > LIMITE_INPUT:
            full_prompt = full_prompt[:LIMITE_INPUT].rstrip() + "\n...(recortado por límite de caracteres de You.com)"

        payload = {
            "input": full_prompt,
            "research_effort": research_effort,
            "background": False,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=35)
            if not response.ok:
                # Antes esto se perdía: raise_for_status() solo da el texto
                # genérico "422 Client Error...", sin el detalle real que
                # You.com manda en el body explicando qué campo falló.
                try:
                    detalle = response.json()
                except Exception:
                    detalle = response.text[:800]
                return (
                    f"Error de You.com ({response.status_code}): {detalle}"
                )
            data = response.json()

            if isinstance(data, dict):
                if "output" in data and isinstance(data["output"], dict) and "content" in data["output"]:
                    return data["output"]["content"].strip()
                for key in ("answer", "content", "text", "result"):
                    if key in data and isinstance(data[key], str):
                        return data[key].strip()
                if "output" in data and isinstance(data["output"], str):
                    return data["output"].strip()

            return str(data)
        except Exception as error:
            return f"Error leyendo respuesta de You.com: {error}"

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