FROM node:20-slim AS ui

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# curl нужен healthcheck-у compose, ffmpeg/ffprobe — постобработке видео.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md prefect.yaml ./
COPY src ./src

# Собранный UI — генерируемый артефакт: в wheel он не попадает (gitignore),
# поэтому приложение работает из /app/src по PYTHONPATH, где файлы лежат рядом.
COPY --from=ui /build/src/content_factory/api/ui ./src/content_factory/api/ui

RUN pip install .

COPY main.py ./

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/app_data \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
