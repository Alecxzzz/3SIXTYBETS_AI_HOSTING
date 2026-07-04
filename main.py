
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from engine.brain import Brain


app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con motor de decisión, búsqueda web y análisis de apuestas.",
    version="3.0"
)

app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="...",
    version="2.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://threesixtybets-chat.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

brain = Brain()


class Chat(BaseModel):
    mensaje: str
    buscar: bool = True


@app.get("/", response_class=PlainTextResponse)
def inicio():
    return "3SIXTYBETS AI WORKSPOT funcionando. Entra a /docs para probar."


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):

    if not data.buscar:
        return "La búsqueda web está desactivada. Activa buscar=true para usar el motor de decisión."

    respuesta = brain.procesar(data.mensaje)

    return respuesta