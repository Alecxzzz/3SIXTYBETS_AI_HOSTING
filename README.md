# 3SIXTYBETS AI WORKSPOT

## Render

Build Command:
pip install -r requirements.txt

Start Command:
uvicorn main:app --host 0.0.0.0 --port $PORT

Environment Variables:
API_KEY = tu API key de OpenRouter
BASE_URL = https://openrouter.ai/api/v1
MODEL = cohere/north-mini-code:free

Probar:
Abre /docs y usa POST /chat

Ejemplo:
{
  "mensaje": "Analiza Uruguay vs España",
  "buscar": true
}
