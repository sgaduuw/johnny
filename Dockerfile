# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14

# ---------------------------------------------------------- base
# Python + poetry + main runtime deps only. Shared by test and prod
# so the slow `poetry install` layer caches across both targets.
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install Poetry into the system Python, app deps into a
# project-local `.venv` (POETRY_VIRTUALENVS_IN_PROJECT=true).
# Earlier this image set VIRTUALENVS_CREATE=false so both Poetry
# and the app deps shared one Python; under Poetry 2.x + PEP 621,
# `poetry install` would resolve `packaging==24.0` against the
# lockfile and downgrade Poetry's own dep, crashing the second
# `poetry install` call with "No module named 'packaging.licenses'".
# Project-local venv keeps Poetry's deps isolated from the app's.
RUN pip install --no-cache-dir "poetry>=2.0,<3.0"

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

COPY README.md ./
RUN pip install --no-cache-dir --no-deps -e .

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

COPY README.md ./
RUN pip install --no-cache-dir --no-deps -e .

ARG UID=10001
RUN useradd --uid ${UID} --create-home --shell /usr/sbin/nologin johnny \
    && mkdir -p /data \
    && chown johnny:johnny /data
USER johnny
VOLUME ["/data"]

EXPOSE 8000 8001
ENTRYPOINT ["/entrypoint.sh"]
