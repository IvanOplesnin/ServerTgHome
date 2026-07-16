FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 -o Acquire::https::Timeout=120 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 -o Acquire::https::Timeout=120 \
      install -y --no-install-recommends ffmpeg ca-certificates chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY server_tg_home ./server_tg_home

RUN pip install --retries 10 --timeout 120 .

CMD ["server-tg-home", "api", "--host", "0.0.0.0", "--port", "8080"]
