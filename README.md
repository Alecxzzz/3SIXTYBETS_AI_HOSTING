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

# Aiven PostgreSQL (SSL requerido)
DATABASE_URL=postgres://usuario:password@host:puerto/database?sslmode=require
# O usa PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE / PGSSLMODE
ADMIN_USERNAME=admin
ADMIN_PASSWORD=cambia_esta_contrasena
FRONTEND_ORIGINS=http://localhost:5173,https://threesixtybets-chat.vercel.app
```

`DATABASE_URL` conecta el backend con PostgreSQL. Si tu hosting no usa URL, configura `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` y `PGSSLMODE`.

## Auth y base de datos

El backend crea automaticamente tablas PostgreSQL con:
- `users`
- `sessions`
- `chat_messages`
- `redeem_keys`
- `credit_transactions`

La cuenta principal se crea automaticamente si configuras `ADMIN_USERNAME` y `ADMIN_PASSWORD`. Desde esa cuenta puedes crear keys `SIXTYBETS-XXXX-XXXX`.

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
