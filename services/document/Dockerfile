FROM python:3.12-slim AS builder
WORKDIR /build
COPY services/document/pyproject.toml ./
COPY services/document/app ./app
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IDP_PACKAGE_PATH=/srv
# Debian Trixie renamed the GLib runtime package.
RUN apt-get update \
    && glib_package=libglib2.0-0 \
    && if apt-cache show libglib2.0-0t64 >/dev/null 2>&1; then glib_package=libglib2.0-0t64; fi \
    && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng pandoc "$glib_package" \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10002 document \
    && useradd --uid 10002 --gid document --no-create-home document \
    && install -d -m 750 -o document -g document /var/lib/lumi-document
COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index /tmp/wheels/* && rm -rf /tmp/wheels
WORKDIR /srv
COPY services/document/migrations ./migrations
COPY services/document/alembic.ini ./
COPY services/rag/idp /srv/idp
USER document
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-proxy-headers"]
