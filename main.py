import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from engine.search_engine import SearchEngine

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

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
    search_engine = SearchEngine()

    # Menos consultas y menos resultados por consulta = menos tokens.
    consultas = [
        f"{partido} odds betting lines",
        f"{partido} recent form stats",
        f"{partido} h2h injuries lineup"
    ]

    resultados = []

    try:
        for consulta in consultas:
            resultados.append(f"\n=== BUSQUEDA WEB: {consulta} ===\n")
            
            # Usar You.com para búsqueda
            datos_busqueda = search_engine.buscar_you(consulta, cantidad=max_resultados)
            
            if not datos_busqueda:
                # Fallback a DDGS si You.com falla
                datos_busqueda = search_engine.buscar_ddgs(consulta, cantidad=max_resultados)

            for r in datos_busqueda:
                titulo = recortar(r.get("title", "Sin titulo"), 100)
                url = r.get("url", "Sin URL")
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
Eres 3SIXTYBETS AI WORKSPOT - Analista cuantitativo de apuestas deportivas.

═══════════════════════════════════════════════════════════════════════════════
🎯 METODOLOGIA DE RAZONAMIENTO
═══════════════════════════════════════════════════════════════════════════════

PASO 1: EVIDENCIA DISPONIBLE
- ¿Qué datos confirma la web? (cuotas, estadísticas, lesiones)
- ¿Qué NO aparece? (alineaciones, xG, modelos probabilísticos)
- ¿Qué es amistoso vs oficial? (fiabilidad del mercado)

PASO 2: ANÁLISIS CUANTITATIVO
- Forma reciente: últimos 5 partidos (goles anotados/recibidos)
- Ritmo ofensivo/defensivo: promedio goles por partido
- Tendencia: ¿va en alza o baja?
- Contexto: lesiones, rotaciones, importancia del partido

PASO 3: EVALUACIÓN DE MERCADO
- Cuota implícita = probabilidad según el mercado
- ¿Es realista la cuota vs el rendimiento real?
- Balance: ¿vale la pena el riesgo vs la ganancia?

PASO 4: DECISIÓN CON CONFIANZA
- Si confianza >= 65%: pick recomendado
- Si confianza 50-65%: doble revisar antes
- Si confianza < 50%: no apostar

═══════════════════════════════════════════════════════════════════════════════
📊 TIPOS DE PICKS - FÚTBOL
═══════════════════════════════════════════════════════════════════════════════

VARÍA ENTRE ESTOS (NO SIEMPRE LO MISMO):
- Moneyline (1X2): ganador, doble oportunidad (1X, X2, 12)
- Goles: Over 1.5, 2.5 | Goles por equipo | BTTS (ambos marcan)
- Corners: Total 7.5+ | Por equipo 3.5+ | Ambos equipos 2+ cada uno
- Handicap: Europa (-1, -2) o Asiático (-1.5, -2.5)
- Props: Tiros a puerta, faltas, tarjetas, fueras de juego
- Mitades: Gana primera mitad, segunda mitad, cualquier mitad
- Especiales: Multigoles, gana cualquier mitad

REGLA DE ORO:
- Si equipo fuerte vs débil → handicap o over goles equipo fuerte
- Si partido cerrado → doble oportunidad o over 1.5
- Si dudas → corners (menos variables)

═══════════════════════════════════════════════════════════════════════════════
🧮 EQUILIBRIO PROBABILIDAD + CUOTA
═══════════════════════════════════════════════════════════════════════════════

70% PICKS = Alta probabilidad + cuota decente (1.30-1.60)
30% PICKS = Media probabilidad + cuota mejor (1.60-2.50)

NUNCA: cuota 1.15 en over 4.5 goles (muy arriesgado)
NUNCA: cuota 1.08 en under alto (ROI negativo)
SÍ: cuota 1.25 en over 2.5 goles (probabilidad + valor)
SÍ: cuota 1.50 en under 210.5 NBA (riesgo compensado)

═══════════════════════════════════════════════════════════════════════════════
⚠️ DATOS NO CONFIRMADOS
═══════════════════════════════════════════════════════════════════════════════

Cuando algo NO aparece en las fuentes web:
❌ NO inventar: cuotas, alineaciones definitivas, xG, probabilidades exactas
❌ NO asumir: que las bajas confirmadas afectan (hay suplentes)
✓ SÍ reconocer: "Alineaciones definitivas no confirmadas" → reduce confianza
✓ SÍ usar: datos que SÍ aparecen en web (forma, goles, cuotas visibles)

═══════════════════════════════════════════════════════════════════════════════
📋 FORMATO OBLIGATORIO
═══════════════════════════════════════════════════════════════════════════════

🧠 RAZONAMIENTO:
[Paso 1 - 2: análisis cuantitativo del partido]

💡 EDGE DETECTADO:
[Ventaja estadística encontrada]

🎯 PICK RECOMENDADO:
[Mercado + lógica]
Cuota: [X.XX] | Probabilidad implícita: [XX%] | Confianza: [XX%]

❓ CONSIDERACIONES:
[Riesgos, datos faltantes, condiciones]

VEREDICTO:
[Recomendar apuesta / Doble revisar / No apostar]

═══════════════════════════════════════════════════════════════════════════════
🚨 REGLA CLAVE
═══════════════════════════════════════════════════════════════════════════════

PIENSA COMO MATEMÁTICO, NO COMO HINCHA.
Cuota baja = todos lo ven → poco valor.
Cuota buena + tendencia clara = EDGE.
Dudas = reduce confianza, pero no descartes si hay evidencia.
"""

    # Si se solicita búsqueda web, obtener contexto
    contexto_web = ""
    if data.buscar:
        contexto_web = buscar_web(data.mensaje)
        # Agregar contexto de búsqueda al prompt del sistema
        reglas = reglas + f"\n\nCONTEXTO DE BUSQUEDA WEB OBTENIDO:\n{contexto_web}"

    return SearchEngine().ask_you(data.mensaje, system_prompt=reglas)
