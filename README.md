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

### IA del dashboard (picks): rotación de claves y proveedor de respaldo

El plan gratuito de OpenRouter se agota **por cuenta** (50 peticiones diarias a
modelos `:free`): cuando llega a cero, **todos** los modelos devuelven 429
`free-models-per-day`, así que rotar modelos no recupera el servicio. El
generador de picks (`ai/dashboard_ia.py`) resuelve eso en dos niveles: rota de
**clave** (otra cuenta = otra cuota) y, si todas están agotadas, salta a un
**proveedor de respaldo**.

```env
# Proveedor principal (OpenRouter). Admite varias claves separadas por comas:
# se usan en orden y la agotada queda en pausa DASHBOARD_AI_PAUSA_KEY segundos.
DASHBOARD_AI_API_KEY=sk-or-v1-...
DASHBOARD_AI_API_KEY_FALLBACK=sk-or-v1-...   # alias para una clave extra
DASHBOARD_AI_BASE_URL=https://openrouter.ai/api/v1/chat/completions
DASHBOARD_AI_MODEL=modelo-principal:free
DASHBOARD_AI_FALLBACK=modelo-de-respaldo:free

# Proveedor de respaldo (Groq). Si no se define API_KEY usa AI36_GROQ_API_KEY
# o GROQ_API_KEY, así que normalmente NO hace falta configurar nada más.
DASHBOARD_AI_RESPALDO=true
DASHBOARD_AI_RESPALDO_API_KEY=gsk_...
DASHBOARD_AI_RESPALDO_BASE_URL=https://api.groq.com/openai/v1/chat/completions
DASHBOARD_AI_RESPALDO_MODEL=openai/gpt-oss-120b
DASHBOARD_AI_RESPALDO_MODELOS_EXTRA=openai/gpt-oss-20b,qwen/qwen3.8-27b
```

`GET /dashboard/salud` devuelve `ia_claves` con el estado de cada clave
(`proveedor`, `endpoint`, `en_pausa`, `reanuda_en_s`, `modelos` que se probarán
con ella) y la clave **enmascarada**; nunca expone la credencial completa.
`ia_consumo.ultimo_error` guarda el motivo del último fallo de la IA (404 de un
modelo retirado, 402 de la cuenta, 413 del ITPM...), que antes solo aparecía en
los logs del hosting.

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
