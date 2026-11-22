# Multi-stage: build the SPA, then serve it from the API container.
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-deps -e . && pip install \
    "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.30,<1.0" "sqlalchemy[asyncio]>=2.0.30,<2.1" \
    "aiosqlite>=0.20" "asyncpg>=0.29" "pydantic>=2.7,<3.0" "pydantic-settings>=2.3,<3.0" \
    "python-multipart>=0.0.9" "pyjwt>=2.8,<3.0" "httpx>=0.27,<1.0" "cryptography>=42.0" "numpy>=1.26"

COPY --from=web /web/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 resonance && mkdir -p /app/backend/data && chown -R resonance /app
USER resonance

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -sf http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
