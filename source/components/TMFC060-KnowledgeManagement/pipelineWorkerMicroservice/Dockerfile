# TMFC060 pipeline worker. Build from this folder: docker build -t tmforumodacanvas/tmfc060-pipeline-worker:dev .
FROM ghcr.io/astral-sh/uv:0.7.18 AS uv

FROM python:3.12.16-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --no-dev: the knowledge API path dependency is for the tests only and is not in this build context
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12.16-slim-bookworm
ARG SOURCE_DATE_EPOCH
ARG CICD_BUILD_TIME
ARG GIT_COMMIT_SHA
LABEL org.opencontainers.image.title="tmfc060-pipeline-worker" \
      org.opencontainers.image.source="https://github.com/tmforum-oda/oda-canvas" \
      org.opencontainers.image.revision="${GIT_COMMIT_SHA}" \
      org.opencontainers.image.created="${CICD_BUILD_TIME}"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY worker/ ./worker/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8082
CMD ["python", "-m", "worker"]
