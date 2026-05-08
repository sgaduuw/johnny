# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN pip install --no-cache-dir poetry==1.8.5

# Install runtime deps first (leverages layer cache when only source changes).
COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    --mount=type=cache,target=/root/.cache/pip \
    poetry install --no-root --only main

# App source.
COPY johnny ./johnny
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Install the project itself (registers `johnny` script entry-point).
RUN poetry install --only-root

# Non-privileged user; /data is the volume mount-point for SQLite.
ARG UID=10001
RUN useradd --uid ${UID} --create-home --shell /usr/sbin/nologin johnny \
    && mkdir -p /data \
    && chown johnny:johnny /data
USER johnny
VOLUME ["/data"]

EXPOSE 8000 8001
ENTRYPOINT ["/entrypoint.sh"]
