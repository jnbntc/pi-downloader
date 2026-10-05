FROM python:3.11-slim-bookworm

# Evitar escritura de bytecode y buffer en stdout/stderr para logs en tiempo real de Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema: ffmpeg (para yt-dlp), 7zip y unrar (para extracción en scratchpad)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    7zip \
    unrar-free \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Copiar el código del orquestador
COPY *.py ./

# Crear usuario sin privilegios para ejecución segura (coincidiendo con UID 1000 del host Linux)
RUN useradd -u 1000 -U -M -s /bin/false appuser && \
    chown -R appuser:appuser /app
USER appuser

CMD ["python", "-u", "main.py"]
