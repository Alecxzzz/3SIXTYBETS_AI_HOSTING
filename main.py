import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from engine.search_engine import SearchEngine

# ==============================
# CONFIGURACION PARA HOSTING
# ==============================
# Este backend solo soporta You.com.
# No inicializa OpenAI ni Groq en el arranque.

YOU_API_KEY = os.getenv("YOU_API_KEY") or os.getenv("YOU_SEARCH_API_KEY") or ""
YOU_BASE_URL = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")

app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con busqueda web automatica.",
    version="2.0"
)

# CORS: permitir el frontend
frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,https://threesixtybets-chat.vercel.app"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in frontend_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Chat(BaseModel):
    mensaje: str
    buscar: bool = True


def limpiar_consulta(mensaje: str) -> str:
    texto = mensaje.strip()
    texto_lower = texto.lower()

    frases = [
        "analiza el partido",
        "analiza partido",
        "analiza",
        "partido",
        "juego",
        "match",
        "edge",
        "apuesta",
        "pronostico",
        "pronóstico"
    ]

    for frase in frases:
        if texto_lower.startswith(frase):
            texto = texto[len(frase):].strip()
            texto_lower = texto.lower()

    return texto if texto else mensaje


# Limite de seguridad para no exceder el TPM (tokens por minuto) del modelo.
# Groq free/on_demand tiers suelen tener limites bajos (p.ej. 8000 TPM), asi
# que recortamos el contexto web ANTES de enviarlo, en vez de dejar que la
# API rechace la peticion por "Request too large".
MAX_CHARS_CONTEXTO_WEB = int(os.getenv("MAX_CHARS_CONTEXTO_WEB", "3500"))
MAX_CHARS_POR_RESULTADO = int(os.getenv("MAX_CHARS_POR_RESULTADO", "220"))


def recortar(texto: str, limite: int) -> str:
    texto = texto or ""
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + "..."


def buscar_web(mensaje: str, max_resultados: int = 2) -> str:
    partido = limpiar_consulta(mensaje)

    # Menos consultas y menos resultados por consulta = menos tokens.
    consultas = [
        f"{partido} odds betting lines",
        f"{partido} recent form stats",
        f"{partido} h2h injuries lineup"
    ]

    resultados = []

    try:
        with DDGS() as ddgs:
            for consulta in consultas:
                resultados.append(f"\n=== BUSQUEDA WEB: {consulta} ===\n")

                for r in ddgs.text(consulta, max_results=max_resultados):
                    titulo = recortar(r.get("title", "Sin titulo"), 100)
                    url = r.get("href", "Sin URL")
                    contenido = recortar(r.get("body", "Sin contenido"), MAX_CHARS_POR_RESULTADO)

                    resultados.append(
                        f"""Titulo: {titulo}
URL: {url}
Contenido: {contenido}
"""
                    )

    except Exception as e:
        return f"No se pudo buscar en web. Error: {e}"

    if not resultados:
        return "No se encontraron resultados web."

    contexto_final = "\n".join(resultados)

    # Tope duro global: pase lo que pase, nunca mandamos mas de esto.
    if len(contexto_final) > MAX_CHARS_CONTEXTO_WEB:
        contexto_final = contexto_final[:MAX_CHARS_CONTEXTO_WEB].rstrip() + "\n...(contexto recortado por limite de tokens)"

    return contexto_final


@app.get("/", response_class=PlainTextResponse)
def inicio():
    return "Test"


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):
    if not YOU_API_KEY:
        return (
            "ERROR: Falta YOU_API_KEY o YOU_SEARCH_API_KEY.\n"
            "En Render agrega la clave de You.com y la variable YOU_BASE_URL."
        )

    reglas = """
Eres 3SIXTYBETS AI WORKSPOT.

ROL:
Eres un analista cuantitativo profesional especializado en apuestas deportivas con enfoque en Expected Value (+EV).

REGLA CRITICA:
Cuando el usuario mencione un partido, debes usar la busqueda web incluida.
No respondas solo con conocimiento general.
Si la busqueda web no trae datos suficientes, dilo claramente.

OBJETIVO:
Detectar una ventaja estadistica real basada en:
- estadisticas recientes
- cuotas reales si aparecen en las fuentes
- contexto reciente
- lesiones o alineaciones si aparecen
- forma reciente
- tendencias sostenibles
- mercados con posible value

NO INVENTAR:
- cuotas
- estadisticas
- lesiones
- mercados
- alineaciones
- fechas
- competiciones

Si un dato no aparece en la informacion web, escribe:
\"No aparece confirmado en las fuentes consultadas.\"

DEPORTES SOPORTADOS:
Futbol, NBA, MLB, Tenis, NHL, UFC/MMA.

RESPONDE SIEMPRE CON You.com como proveedor principal.
No uses ni Groq ni OpenAI ni rutas alternativas.
"""

    return SearchEngine().ask_you(data.mensaje, system_prompt=reglas)
