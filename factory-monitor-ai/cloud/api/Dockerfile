FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY cloud ./cloud
RUN pip install --upgrade pip && pip install ".[api]"

EXPOSE 8000

CMD ["uvicorn", "cloud.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
