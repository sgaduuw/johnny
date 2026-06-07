# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14

# ---------------------------------------------------------- base
# Python plus uv plus main runtime deps. Shared by test and prod
# so the slow `uv sync` layer caches across both targets.
FROM python:${PYTHON_VERSION}-slim AS base

# Bring in uv as a static binary from Astral's official image.
# Pinning to a specific uv version is also reasonable; latest
# tracks the most recent stable release.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# UV_LINK_MODE=copy avoids hardlink errors when uv's cache and the
# venv are on different filesystems (the BuildKit cache mount and
# the image layer count as different filesystems for hardlink
# purposes). UV_PYTHON_DOWNLOADS=never tells uv not to fetch its
# own Python; use the interpreter from the python:slim base.
# UV_PROJECT_ENVIRONMENT pins the venv path so it matches what the
# test and prod stages (and entrypoint.sh's PATH expectation) use.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Sync runtime deps only. No project install (the source isn't here
# yet), no dev deps. This layer caches across rebuilds when only
# johnny/ source changes; same shape as the previous
# `--no-root --only main` install step.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------- test
# Adds dev deps, the tests/ tree, and the project itself.
# Default CMD runs pytest with the same coverage floor the
# Dockerfile has used historically (85%). Used by CI as
# `docker run --rm johnny:test`; never deployed.
FROM base AS test

# Add dev deps. Project still not installed; the next step copies
# source and runs the project-install sync.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY johnny ./johnny
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests
COPY README.md ./

# Install johnny into the venv (registers the `johnny` console
# script and exposes johnny.__version__ via importlib.metadata).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

CMD ["pytest", "--cov=johnny", "--cov-report=term", "--cov-fail-under=85"]

# ---------------------------------------------------------- prod
# Lean runtime image. Default target when --target is omitted.
# What compose.yaml builds and what the johnny-{web,api,tasks}
# services actually run.
FROM base AS prod

COPY johnny ./johnny
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY README.md ./

# Install johnny into the venv (without dev deps).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ARG UID=10001
RUN useradd --uid ${UID} --create-home --shell /usr/sbin/nologin johnny \
    && mkdir -p /data \
    && chown johnny:johnny /data
USER johnny
VOLUME ["/data"]

EXPOSE 8000 8001
ENTRYPOINT ["/entrypoint.sh"]
