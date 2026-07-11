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
YOU_SEARCH_API_KEY=tu_you_key
YOU_SEARCH_URL=https://api.ydc-index.io/search

DATABASE_PATH=threesixtybets.db
FRONTEND_ORIGINS=http://localhost:5173,https://threesixtybets-chat.vercel.app
```

`GROQ_BASE_URL`, `GROQ_MODEL`, `YOU_BASE_URL`, `YOU_MODEL`, `YOU_SEARCH_API_KEY`, `YOU_SEARCH_URL`, `DATABASE_PATH` y `FRONTEND_ORIGINS` son opcionales si quieres usar los defaults del codigo.

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
