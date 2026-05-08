# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------- base
# Python + poetry + main runtime deps only. Shared by test and prod
# so the slow `poetry install` layer caches across both targets.
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN pip install --no-cache-dir "poetry==1.8.5"

COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    --mount=type=cache,target=/root/.cache/pip \
    poetry install --no-root --only main

# ---------------------------------------------------------- test
# Adds dev deps + the tests/ tree. Default CMD runs pytest with the
# same coverage floor as the runner-side CI job. Used by CI as
# `docker run --rm johnny:test`; never deployed.
FROM base AS test

RUN --mount=type=cache,target=/root/.cache/pypoetry \
    --mount=type=cache,target=/root/.cache/pip \
    poetry install --no-root

COPY johnny ./johnny
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests

RUN poetry install --only-root

CMD ["pytest", "--cov=johnny", "--cov-report=term", "--cov-fail-under=85"]

# ---------------------------------------------------------- prod
# Lean runtime image. Default target when --target is omitted. This is
# what compose.yaml builds and what the johnny-{web,api,tasks} services
# actually run.
FROM base AS prod

COPY johnny ./johnny
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN poetry install --only-root

ARG UID=10001
RUN useradd --uid ${UID} --create-home --shell /usr/sbin/nologin johnny \
    && mkdir -p /data \
    && chown johnny:johnny /data
USER johnny
VOLUME ["/data"]

EXPOSE 8000 8001
ENTRYPOINT ["/entrypoint.sh"]
