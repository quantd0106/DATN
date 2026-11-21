import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT_ROOT = REPO_ROOT / "services/document"


def test_compose_document_startup_and_isolation():
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "document_data" in compose["volumes"]

    for name in ("document-db-init", "document-migrate", "document", "document-worker"):
        service = services[name]
        assert service["read_only"] is True
        assert service["tmpfs"] == ["/tmp"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["cap_drop"] == ["ALL"]
        assert service["depends_on"]["db"]["condition"] == "service_healthy"

    init = services["document-db-init"]
    assert init["image"] == "postgres:16-alpine"
    assert init["command"] == ["sh", "/init/init-document-db.sh"]
    assert init["volumes"] == [
        "./services/document/docker/init-document-db.sh:/init/init-document-db.sh:ro"
    ]
    assert init["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    migration = services["document-migrate"]
    assert migration["command"] == ["python", "-m", "alembic", "upgrade", "head"]
    assert migration["depends_on"]["document-db-init"]["condition"] == (
        "service_completed_successfully"
    )
    document = services["document"]
    worker = services["document-worker"]
    database_url = migration["environment"]["DOCUMENT_DATABASE_URL"]
    for service in (migration, document, worker):
        assert service["image"] == migration["image"]
        assert service["build"] == {"context": ".", "dockerfile": "services/document/Dockerfile"}
        assert service["environment"]["DOCUMENT_DATABASE_URL"] == database_url
        assert "DATABASE_URL" not in service["environment"]

    for service in (document, worker):
        assert service["depends_on"]["document-migrate"]["condition"] == (
            "service_completed_successfully"
        )
        assert service["volumes"] == ["document_data:/var/lib/lumi-document"]

    assert document["depends_on"]["core"]["condition"] == "service_healthy"
    assert document["ports"] == ["127.0.0.1:${DOCUMENT_PORT:-8001}:8001"]
    assert "http://127.0.0.1:8001/health/ready" in " ".join(document["healthcheck"]["test"])
    assert worker["command"] == ["python", "-m", "app.worker"]

    web = services["web"]
    assert web["environment"]["DOCUMENT_API_URL"] == "${DOCUMENT_API_URL:-http://document:8001}"
    assert document["environment"]["CORE_API_URL"] == "${CORE_API_URL:-http://core:8000}"
    assert web["depends_on"]["document"] == {"condition": "service_healthy"}
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^(?:DOCUMENT_API_URL|CORE_API_URL)\s*=", env_example, re.MULTILINE)


def test_document_image_copies_only_document_and_idp():
    dockerfile = (DOCUMENT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder, runtime = dockerfile.split("FROM python:3.12-slim AS runtime\n")
    assert re.findall(r"^USER\s+(\S+)$", runtime, re.MULTILINE) == ["document"]
    assert "groupadd --gid 10002 document" in runtime
    assert "useradd --uid 10002 --gid document --no-create-home document" in runtime
    assert "IDP_PACKAGE_PATH=/srv" in runtime
    for package in (
        "poppler-utils",
        "tesseract-ocr",
        "tesseract-ocr-vie",
        "tesseract-ocr-eng",
        "pandoc",
    ):
        assert package in runtime.split()

    copy_pattern = r"^(?:COPY|ADD)\s+(.+)$"
    assert re.findall(copy_pattern, builder, re.MULTILINE) == [
        "services/document/pyproject.toml ./",
        "services/document/app ./app",
    ]
    assert re.findall(copy_pattern, runtime, re.MULTILINE) == [
        "--from=builder /wheels /tmp/wheels",
        "services/document/migrations ./migrations",
        "services/document/alembic.ini ./",
        "services/rag/idp /srv/idp",
    ]

    ignore_rules = (DOCUMENT_ROOT / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    assert ignore_rules.splitlines() == [
        "*",
        "!services/document/pyproject.toml",
        "!services/document/alembic.ini",
        "!services/document/app",
        "!services/document/app/**",
        "!services/document/migrations",
        "!services/document/migrations/**",
        "!services/rag/idp",
        "!services/rag/idp/**",
        "**/__pycache__",
        "**/*.pyc",
        "**/*.pyo",
        "**/.pytest_cache",
        "**/.venv",
        "**/tests",
    ]


def test_db_init_uses_lf_and_revokes_cross_schema_access():
    script = (DOCUMENT_ROOT / "docker/init-document-db.sh").read_bytes()
    assert b"\r" not in script
    sql = script.decode("utf-8")
    assert "-v ON_ERROR_STOP=1" in sql
    assert "ALTER ROLE %I SET search_path = document', :'doc_user'" in sql
    for grant in (
        "REVOKE ALL ON SCHEMA public",
        "REVOKE ALL ON SCHEMA core",
        "REVOKE ALL ON ALL TABLES IN SCHEMA core",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA core",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA core REVOKE ALL ON TABLES",
    ):
        assert f"{grant} FROM %I', :'doc_user'" in sql
