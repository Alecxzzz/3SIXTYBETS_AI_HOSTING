import os
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from openai import OpenAI
from ddgs import DDGS

# ==============================
# CONFIGURACION PARA HOSTING
# ==============================
# En Render agrega estas variables:
# API_KEY = tu API Key de OpenRouter
# BASE_URL = https://openrouter.ai/api/v1
# MODEL = cohere/north-mini-code:free

API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.getenv("MODEL", "cohere/north-mini-code:free")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con busqueda web automatica.",
    version="2.0"
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
    return "3SIXTYBETS AI WORKSPOT funcionando. Entra a /docs para probar."


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):
    if not API_KEY:
        return (
            "ERROR: Falta API_KEY.\n"
            "En Render agrega API_KEY, BASE_URL y MODEL en Environment Variables."
        )

    if data.buscar:
        contexto_web = buscar_web(data.mensaje, max_resultados=2)
    else:
        contexto_web = "Busqueda web desactivada."

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
"No aparece confirmado en las fuentes consultadas."

DEPORTES SOPORTADOS:
Futbol, NBA, MLB, Tenis, NHL, UFC/MMA.

REGLAS:
- Maximo 4 picks por partido.
- No forzar picks si no hay edge.
- No repetir picks correlacionados.
- No abusar de ML, Over 2.5 o BTTS.
- Priorizar mercados variados: handicaps, corners, props, team totals, tiros, tarjetas, PRA, rebotes, asistencias, sets, aces.

FUTBOL:
Priorizar doble oportunidad, handicap, gana cualquier mitad, team totals, corners minimo 7.5, tarjetas, tiros a puerta, jugador marca/asiste, over/under goles y BTTS solo con evidencia fuerte.

NBA:
Priorizar handicap, team totals, PRA bajos, puntos/rebotes/asistencias/triples de jugador, primera mitad, primer cuarto y total partido solo si hay ritmo claro.

MLB:
Priorizar pitchers, bullpen, hits, runs, strikeouts y splits local/visitante.

TENIS:
Priorizar handicap juegos, total juegos, ambos ganan set, sets exactos, aces y breaks.

CALCULO:
Si hay cuota real:
Probabilidad implicita = 1 / cuota.
Value = Probabilidad estimada - Probabilidad implicita.

Si no hay cuota real:
No inventes cuota. Puedes dar probabilidad estimada aproximada y aclarar que falta validar cuota.

FORMATO FINAL OBLIGATORIO:

CONTEXTO DEL EVENTO
- Deporte:
- Partido:
- Competicion:
- Fecha:

ESTADISTICAS RECIENTES
- Dato 1:
- Dato 2:
- Dato 3:

LECTURA DEL PARTIDO
- Narrativa principal:
- Ritmo esperado:
- Matchup clave:

MERCADO Y CUOTAS
- Mercado:
- Linea:
- Cuota:
- Casa/Fuente:

MODELO
- Probabilidad estimada:
- Probabilidad implicita:
- Value:

VALUE DETECTADO Y PICKS

1) PICK — cuota

- Implicita:
- Modelo:
- Value:
- Stake:

- Argumento corto:

FUENTES CONSULTADAS:
- URL 1
- URL 2
- URL 3
"""

    prompt_usuario = f"""
PREGUNTA DEL USUARIO:
{data.mensaje}

INFORMACION ENCONTRADA EN WEB:
{contexto_web}

INSTRUCCIONES:
Usa la informacion web anterior como fuente principal.
Si la informacion es insuficiente, dilo.
No inventes cuotas, estadisticas ni mercados.
Incluye URLs usadas en FUENTES CONSULTADAS.
"""

    def llamar_modelo(contenido_usuario: str):
        return client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": reglas},
                {"role": "user", "content": contenido_usuario}
            ],
            temperature=0.2
        )

    try:
        respuesta = llamar_modelo(prompt_usuario)
        return respuesta.choices[0].message.content

    except Exception as e:
        error_texto = str(e)

        # Si el proveedor rechaza por tamaño (p.ej. "Request too large",
        # "too many tokens", error 413), reintentamos una vez con un
        # contexto web mucho mas pequeno en vez de fallar directamente.
        if "too large" in error_texto.lower() or "413" in error_texto or "tokens per minute" in error_texto.lower():
            contexto_reducido = recortar(contexto_web, 1200)
            prompt_reducido = f"""
PREGUNTA DEL USUARIO:
{data.mensaje}

INFORMACION ENCONTRADA EN WEB:
{contexto_reducido}

INSTRUCCIONES:
Usa la informacion web anterior como fuente principal.
Si la informacion es insuficiente, dilo.
No inventes cuotas, estadisticas ni mercados.
Incluye URLs usadas en FUENTES CONSULTADAS.
"""
            try:
                respuesta = llamar_modelo(prompt_reducido)
                return respuesta.choices[0].message.content
            except Exception as e2:
                return f"No se pudo generar la respuesta (incluso tras reducir el contexto).\n\nDetalle:\n{str(e2)}"

        return f"No se pudo generar la respuesta.\n\nDetalle:\n{error_texto}"