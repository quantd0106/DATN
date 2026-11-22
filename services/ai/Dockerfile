FROM python:3.12-slim AS builder
WORKDIR /build
COPY ai/pyproject.toml ./
COPY ai/app ./app
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[rag]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10002 ai && useradd --uid 10002 --gid ai --no-create-home ai
COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index /tmp/wheels/* && rm -rf /tmp/wheels
WORKDIR /srv
COPY ai/alembic.ini ./
COPY ai/migrations ./migrations
COPY rag ./rag
USER ai
EXPOSE 8001
CMD ["python", "-m", "app"]
