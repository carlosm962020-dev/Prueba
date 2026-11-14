# Imagen del pipeline de ventas (B2).
#
# Construir desde la RAÍZ del repositorio:
#   docker build -f infra/Dockerfile -t pagared/ventas-pipeline:1.0 .
#
# Ejecutar (datos y resultados viven fuera de la imagen):
#   docker run --rm --user "$(id -u):$(id -g)" \
#     -v "$PWD/datos:/app/datos:ro" -v "$PWD/salida:/app/salida" -v "$PWD/logs:/app/logs" \
#     -e SMTP_HOST -e SMTP_USER -e SMTP_PASSWORD -e REPORTE_DESTINATARIOS \
#     pagared/ventas-pipeline:1.0 --fecha-corte 2026-10-27 --enviar
#
# Ninguna credencial se hornea en la imagen: entran por variables de entorno al ejecutar.
# No pude construirla aquí (sin Docker en mi entorno): revisar con un primer `docker build`.
FROM python:3.12-slim

# tzdata para que los logs salgan en hora Colombia. El día de negocio no depende de esto:
# el pipeline resta 5 horas a UTC por su cuenta.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=America/Bogota \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Sin root dentro del contenedor.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

# Las dependencias van primero: mientras requirements.txt no cambie, esta capa se reutiliza.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY etl ./etl

RUN mkdir -p datos salida logs && chown -R app:app /app
USER app

VOLUME ["/app/datos", "/app/salida", "/app/logs"]

# Sin CMD: los argumentos de `docker run` van directo al pipeline.
ENTRYPOINT ["python", "-m", "etl.pipeline"]
