# DeepLearning dans Docker : l'application (FastAPI) + LibreOffice pour les fichiers Word / PowerPoint.
# L'IA (Ollama) reste installée sur le PC pour profiter de la carte graphique.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEEPLEARNING_DATA=/data \
    DEEPLEARNING_HOST=0.0.0.0 \
    DEEPLEARNING_PORT=8765 \
    DL_DOCKER=1 \
    DL_AUTH=1

# LibreOffice sans interface (Writer + Impress) et des polices métriquement compatibles avec celles
# d'Office (Arial/Times → Liberation, Calibri → Carlito, Cambria → Caladea) pour des aperçus fidèles.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-impress-nogui \
      fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY docker/entrypoint.py ./docker/entrypoint.py

# Pas de droits root pour l'application.
RUN useradd --create-home --uid 1000 deeplearning \
 && mkdir -p /data \
 && chown deeplearning:deeplearning /data
USER deeplearning

VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/version', timeout=4)"

CMD ["python", "docker/entrypoint.py"]
