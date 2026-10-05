ARG UV_VERSION=0.11.21
ARG PYTHON_VERSION=3.13

#########################################
## Stage: uv
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

#########################################
## Stage: base
FROM python:${PYTHON_VERSION}-slim AS base

RUN groupadd -r app && useradd -r -d /app -g app -N app \
 && mkdir -p /app && chown app:app /app
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1


#########################################
## Stage: builder
FROM base AS builder

COPY --from=uv /uv /usr/local/bin/uv

USER app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --chown=app:app pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=app:app src ./src
RUN uv sync --locked --no-dev --no-editable


#########################################
## Stage: production
FROM base AS production

# gcc + libc headers to compile the C programs; tini reaps the programs' orphans.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev tini \
 && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["tini", "--"]

USER app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    WEBGCC_HOST=0.0.0.0 \
    WEBGCC_PORT=8000

EXPOSE 8000
CMD ["webgcc"]
