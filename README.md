# 3SIXTYBETS AI WORKSPOT

## Render

Build Command:
`pip install -r requirements.txt`

Start Command:
`uvicorn main:app --host 0.0.0.0 --port $PORT`

## Environment Variables

```env
GROQ_API_KEY=tu_groq_key
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=llama-3.1-8b-instant

YOU_API_KEY=tu_you_key
YOU_BASE_URL=https://api.you.com/v1
YOU_MODEL=you

DATABASE_PATH=threesixtybets.db
```

`GROQ_BASE_URL`, `GROQ_MODEL`, `YOU_BASE_URL`, `YOU_MODEL` y `DATABASE_PATH` son opcionales si quieres usar los defaults del codigo.

## Auth y base de datos

El backend crea automaticamente una base SQLite con:
- `users`
- `sessions`
- `chat_messages`

Para produccion persistente, configura `DATABASE_PATH` en una ruta/volumen que el hosting no borre entre deploys.

## Probar

Abre `/docs` y usa `POST /chat`.

Groq:

```json
{
  "mensaje": "Analiza Uruguay vs Espana",
  "buscar": true,
  "modelo": "groq"
}
```

You.com:

```json
{
  "mensaje": "Analiza Uruguay vs Espana",
  "buscar": true,
  "modelo": "you"
}
```
