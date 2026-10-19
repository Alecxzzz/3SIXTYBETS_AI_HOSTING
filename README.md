# 3SIXTYBETS AI WORKSPOT

## Northflank

Build Command:
`pip install -r requirements.txt`

Start Command:
`uvicorn main:app --host 0.0.0.0 --port $PORT`

## Environment Variables

```env
GROQ_API_KEY=tu_groq_key
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=llama-3.1-8b-instant

# Busqueda web: DuckDuckGo (paquete ddgs), sin API key.

# Aiven PostgreSQL (SSL requerido)
DATABASE_URL=postgres://usuario:password@host:puerto/database?sslmode=require
# O usa PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE / PGSSLMODE
# 36AI (Groq + tools) - segunda IA del ecosistema
AI36_GROQ_API_KEY=tu_groq_key
AI36_GROQ_MODEL=openai/gpt-oss-120b
AI36_GROQ_FALLBACK=openai/gpt-oss-20b

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

Abre `/docs` y usa `POST /chat`. El campo `modelo` selecciona la IA:

- `you` (por defecto): Demian tipster (OpenRouter).
- `36ai`: 365AI (OpenRouter o Groq + function-calling, busca forma/lesiones/H2H con DDGS). En el frontend aparece como "365AI" (id `groq`).

> **Motores:** OpenRouter para Demian, 365AI, dashboard y analista (365AI puede caer a Groq). La busqueda web usa DuckDuckGo (`ddgs`). Si Demian no responde, `/chat` releva a 365AI automaticamente.

`GET /models` lista las IAs disponibles y si están configuradas.

365AI:

```json
{
  "mensaje": "Analiza Uruguay vs Espana",
  "buscar": true,
  "modelo": "36ai"
}
```

Demian (OpenRouter):

```json
{
  "mensaje": "Analiza Uruguay vs Espana",
  "buscar": true,
  "modelo": "you"
}
```
