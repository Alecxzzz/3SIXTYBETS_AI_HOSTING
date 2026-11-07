# Imagen de produccion del backend 3SIXTYBETS (desplegado en Railway).
FROM python:3.11-slim

# Logs sin buffer: Railway muestra los print() en vivo (si no, aparecen a
# trozos y parece que el contenedor esta mudo). Sin .pyc dentro de la imagen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# FFmpeg para el transcodificador /live (H.264 universal para cualquier navegador)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

# PORT portable: Railway (y Render/Cloud Run) inyectan la suya en la variable
# PORT; en hosts que fijan 8080 el fallback ${PORT:-8080} mantiene el
# comportamiento anterior. Sin esto el servicio arranca bien pero el router del
# host no encuentra nada escuchando y devuelve 502 /
# "Application failed to respond".
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
