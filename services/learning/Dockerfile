FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10003 learning && useradd --uid 10003 --gid learning --no-create-home learning
RUN install -d -o learning -g learning /var/lib/learning-blobs
COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index /tmp/wheels/* && rm -rf /tmp/wheels
WORKDIR /srv
COPY alembic.ini ./
COPY migrations ./migrations
USER learning
EXPOSE 8003
CMD ["python", "-m", "app"]
