from ddgs import DDGS


class SearchEngine:

    def __init__(self):
        self.ddgs = DDGS()

    def buscar(self, consulta, cantidad=4):

        datos = []

        try:

            for r in self.ddgs.text(
                consulta,
                max_results=cantidad
            ):

                datos.append({

                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "body": r.get("body", "")

                })

        except:

            pass

        return datos

    def buscar_varias(self, consultas):

        resultados = []

        for consulta in consultas:

            resultados.extend(
                self.buscar(consulta)
            )

        return resultados

    def score(self, resultados):

        score = {

            "stats":0,
            "odds":0,
            "injuries":0,
            "h2h":0,
            "lineups":0,
            "preview":0

        }

        for r in resultados:

            texto = (
                r["title"] +
                " " +
                r["body"]
            ).lower()

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

            if (
                "stats" in texto
                or "average" in texto
                or "last"
                in texto
            ):
                score["stats"] += 1

        return score

    def suficiente(self, score):

        total = 0

        for valor in score.values():

            if valor > 0:

                total += 1

        return total >= 4

    def recopilar(self, partido):

        consultas = [

            partido,

            partido + " odds",

            partido + " injuries",

            partido + " stats",

            partido + " last games",

            partido + " h2h"

        ]

        resultados = self.buscar_varias(
            consultas
        )

        score = self.score(resultados)

        if not self.suficiente(score):

            consultas2 = [

                partido + " betting preview",

                partido + " probable lineup",

                partido + " flashscore",

                partido + " sofascore",

                partido + " statmuse",

                partido + " injuries espn"

            ]

            resultados.extend(

                self.buscar_varias(

                    consultas2

                )

            )

        return resultados