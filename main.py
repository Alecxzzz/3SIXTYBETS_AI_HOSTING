
import os
import sqlite3

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from engine.brain import Brain
from ai.model import modelos_disponibles
from db import (
    create_message,
    create_session,
    create_user,
    delete_session,
    get_user_by_email,
    get_user_by_token,
    init_db,
    list_messages,
    public_user,
    verify_password,
)


app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con motor de decisión, búsqueda web y análisis de apuestas.",
    version="3.0"
)
frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,https://threesixtybets-chat.vercel.app",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

brain = Brain()
init_db()


class Chat(BaseModel):
    mensaje: str
    buscar: bool = True
    modelo: str = "groq"


class AuthRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class MessageRequest(BaseModel):
    role: str
    text: str


def create_auth_response(user):
    token = create_session(user["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


def token_from_header(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token.")

    return authorization.split(" ", 1)[1].strip()


def current_user(authorization: str | None = Header(default=None)):
    token = token_from_header(authorization)
    user = get_user_by_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    return public_user(user)


@app.get("/", response_class=PlainTextResponse)
def inicio():
    return "3SIXTYBETS AI WORKSPOT funcionando. Entra a /docs para probar."


@app.post("/auth/signup")
def signup(data: AuthRequest):
    email = data.email.strip().lower()
    username = (data.name or "").strip()

    if not username or not email or not data.password:
        raise HTTPException(status_code=400, detail="Completa todos los campos.")

    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="La contrasena debe tener al menos 6 caracteres.")

    try:
        user = create_user(username, email, data.password)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email.") from None

    return create_auth_response(user)


@app.post("/auth/signin")
def signin(data: AuthRequest):
    email = data.email.strip().lower()
    user_row = get_user_by_email(email)

    if not user_row or not verify_password(data.password, user_row["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contrasena incorrectos.")

    return create_auth_response(public_user(user_row))


@app.post("/auth/signout")
def signout(authorization: str | None = Header(default=None)):
    token = token_from_header(authorization)
    delete_session(token)
    return {"ok": True}


@app.get("/auth/me")
def me(authorization: str | None = Header(default=None)):
    return current_user(authorization)


@app.get("/models")
def models():
    return modelos_disponibles()


@app.get("/messages")
def messages(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return list_messages(user["id"])


@app.post("/messages")
def save_message(data: MessageRequest, authorization: str | None = Header(default=None)):
    user = current_user(authorization)

    if data.role not in ["user", "ai"]:
        raise HTTPException(status_code=400, detail="Invalid message role.")

    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Message text is required.")

    return create_message(user["id"], data.role, data.text.strip())


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):

    if not data.buscar:
        return "La búsqueda web está desactivada. Activa buscar=true para usar el motor de decisión."

    respuesta = brain.procesar(data.mensaje, data.modelo)

    return respuesta
