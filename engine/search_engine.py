import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ddgs import DDGS


class SearchEngine:
    def __init__(self):
        self.ddgs = DDGS()
        self.you_api_key = os.getenv("YOU_SEARCH_API_KEY") or os.getenv("YOU_API_KEY")
        self.you_search_url = os.getenv("YOU_SEARCH_URL", "https://api.ydc-index.io/search")

    def buscar_you(self, consulta, cantidad=4):
        if not self.you_api_key:
            return []

        params = urlencode({
            "query": consulta,
            "num_web_results": cantidad,
        })
        url = f"{self.you_search_url}?{params}"
        req = Request(
            url,
            headers={
                "X-API-Key": self.you_api_key,
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []

        raw_results = (
            data.get("hits")
            or data.get("results")
            or data.get("web", {}).get("results")
            or []
        )

        datos = []
        for item in raw_results[:cantidad]:
            title = item.get("title") or item.get("name") or ""
            url = item.get("url") or item.get("link") or item.get("href") or ""
            body = (
                item.get("description")
                or item.get("snippet")
                or item.get("body")
                or item.get("summary")
                or ""
            )

            datos.append({
                "title": title,
                "url": url,
                "body": body,
                "source": "you",
            })

        return datos

    def buscar_ddgs(self, consulta, cantidad=4):
        datos = []

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
