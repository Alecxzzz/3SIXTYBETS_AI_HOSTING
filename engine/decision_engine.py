from engine.search_engine import SearchEngine


class DecisionEngine:

    def __init__(self):
        self.search_engine = SearchEngine()

    def detectar_deporte(self, texto):
        texto = texto.lower()

        señales = {
            "MLB": [
                "mlb", "yankees", "red sox", "dodgers", "mets",
                "astros", "braves", "cubs", "phillies",
                "pitcher", "bullpen", "strikeout", "innings"
            ],
            "NBA": [
                "nba", "lakers", "warriors", "celtics", "bucks",
                "heat", "knicks", "nuggets", "mavericks",
                "rebounds", "assists", "points", "triple"
            ],
            "FOOTBALL": [
                "football", "soccer", "real madrid", "barcelona",
                "psg", "arsenal", "chelsea", "liverpool",
                "corners", "shots", "cards", "goles"
            ],
            "TENNIS": [
                "tennis", "atp", "wta", "djokovic", "sinner",
                "alcaraz", "zverev", "medvedev", "sets", "aces"
            ],
            "NHL": [
                "nhl", "oilers", "rangers", "bruins", "panthers",
                "shots on goal", "goalie", "puck line"
            ]
        }

        for deporte, palabras in señales.items():
            for palabra in palabras:
                if palabra in texto:
                    return deporte

        return "UNKNOWN"

    def consultas_por_deporte(self, partido, deporte):

        base = [
            f"{partido} odds betting lines",
            f"{partido} recent form stats",
            f"{partido} injuries lineup news",
            f"{partido} h2h recent matches",
            f"{partido} betting preview"
        ]

        if deporte == "MLB":
            return base + [
                f"{partido} probable pitchers",
                f"{partido} bullpen stats",
                f"{partido} runs hits last 10 games",
                f"{partido} pitcher strikeouts props",
                f"{partido} team total runs odds"
            ]

        if deporte == "NBA":
            return base + [
                f"{partido} injury report",
                f"{partido} last 10 games points",
                f"{partido} pace offensive rating defensive rating",
                f"{partido} player props points rebounds assists",
                f"{partido} team total points odds"
            ]

        if deporte == "FOOTBALL":
            return base + [
                f"{partido} corners stats",
                f"{partido} shots on target stats",
                f"{partido} cards stats",
                f"{partido} expected goals xG",
                f"{partido} probable lineups injuries"
            ]

        if deporte == "TENNIS":
            return base + [
                f"{partido} surface stats",
                f"{partido} h2h tennis abstract",
                f"{partido} aces breaks stats",
                f"{partido} total games odds",
                f"{partido} recent form tennis"
            ]

        if deporte == "NHL":
            return base + [
                f"{partido} goalie confirmed",
                f"{partido} shots on goal stats",
                f"{partido} power play penalty kill",
                f"{partido} team total goals odds",
                f"{partido} goalie saves props"
            ]

        return base + [
            f"{partido} stats",
            f"{partido} prediction",
            f"{partido} preview",
            f"{partido} oddsportal",
            f"{partido} injuries"
        ]

    def evaluar_evidencia(self, resultados):

        score = {
            "odds": 0,
            "stats": 0,
            "injuries": 0,
            "h2h": 0,
            "form": 0,
            "props": 0,
            "lineups": 0
        }

        for r in resultados:
            texto = f"{r.get('title', '')} {r.get('body', '')} {r.get('url', '')}".lower()

            if any(p in texto for p in ["odds", "betting", "line", "cuota"]):
                score["odds"] += 1

            if any(p in texto for p in ["stats", "average", "promedio", "ranking", "rating"]):
                score["stats"] += 1

            if any(p in texto for p in ["injury", "injuries", "lesion", "lesiones", "out", "questionable"]):
                score["injuries"] += 1

            if any(p in texto for p in ["h2h", "head to head", "vs", "history"]):
                score["h2h"] += 1

            if any(p in texto for p in ["last 10", "last games", "recent form", "form", "últimos"]):
                score["form"] += 1

            if any(p in texto for p in ["props", "player prop", "strikeouts", "rebounds", "assists", "shots"]):
                score["props"] += 1

            if any(p in texto for p in ["lineup", "probable", "starting", "alineación"]):
                score["lineups"] += 1

        categorias_con_datos = sum(1 for v in score.values() if v > 0)

        if categorias_con_datos >= 5:
            nivel = "ALTA"
        elif categorias_con_datos >= 3:
            nivel = "MEDIA"
        else:
            nivel = "BAJA"

        return score, nivel

    def resultados_a_texto(self, resultados, limite=40):

        bloques = []

        for i, r in enumerate(resultados[:limite], start=1):
            bloques.append(
                f"""
[{i}]
Titulo: {r.get("title", "Sin titulo")}
URL: {r.get("url", "Sin URL")}
Contenido: {r.get("body", "Sin contenido")}
"""
            )

        return "\n".join(bloques)

    def construir_contexto(self, partido):

        deporte_inicial = self.detectar_deporte(partido)

        consultas = self.consultas_por_deporte(partido, deporte_inicial)

        resultados = []

        for consulta in consultas:
            resultados.extend(
                self.search_engine.buscar(consulta, cantidad=3)
            )

        score, nivel = self.evaluar_evidencia(resultados)

        if nivel == "BAJA":
            consultas_extra = [
                f"{partido} oddsportal",
                f"{partido} sofascore",
                f"{partido} flashscore",
                f"{partido} espn injuries",
                f"{partido} betting picks today",
                f"{partido} statmuse last 10"
            ]

            for consulta in consultas_extra:
                resultados.extend(
                    self.search_engine.buscar(consulta, cantidad=3)
                )

            score, nivel = self.evaluar_evidencia(resultados)

        contexto = f"""
DECISION ENGINE

Partido analizado:
{partido}

Deporte detectado:
{deporte_inicial}

Nivel de evidencia:
{nivel}

Score de evidencia:
{score}

Resultados web recolectados:
{self.resultados_a_texto(resultados)}
"""

        return {
            "partido": partido,
            "deporte": deporte_inicial,
            "nivel_evidencia": nivel,
            "score": score,
            "contexto": contexto
        }