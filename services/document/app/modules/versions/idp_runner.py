"""Run IDP ingestion and read its Canonical and quality artifacts."""

import json
import re
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Literal

from app.common.config import Settings
from app.common.types import DocumentFormat
from app.modules.versions.service import canonical_page_number, project_canonical

SOURCE_SUFFIX: dict[DocumentFormat, str] = {
    "PDF": ".pdf",
    "PPTX": ".pptx",
    "DOCX": ".docx",
    "TXT": ".txt",
    "MARKDOWN": ".md",
}
CANONICAL_SCHEMAS = {"idp-canonical-1", "idp-canonical-2"}
SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
PROCESSING_ERROR_MESSAGE = "Không thể xử lý tài liệu. Vui lòng thử lại."


@dataclass(frozen=True)
class IdpRunOutcome:
    status: Literal["SUCCESS", "NEEDS_REVIEW", "FAILED"]
    canonical_bytes: bytes | None = None
    canonical_schema_version: str | None = None
    page_count: int | None = None
    review_summary: dict | None = None
    error_code: str | None = None
    error_message: str | None = None


def _ensure_idp_importable(config: Settings) -> None:
    if find_spec("idp") is None:
        root = config.idp_package_path or Path(__file__).resolve().parents[4] / "rag"
        root = root.resolve()
        if not (root / "idp").is_dir():
            raise ImportError("IDP package unavailable")
        sys.path.insert(0, str(root))


def _read_report(path: Path) -> dict:
    if not path.is_file():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise TypeError
    return report


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _flag_code(value: object) -> str | None:
    return value if isinstance(value, str) and SAFE_CODE.fullmatch(value) else None


def _review_summary(canonical: dict, report: dict, ingestion: dict, out_dir: Path) -> dict:
    items = set()
    codes = set()
    pages = set()

    def add_flag(flag: object, page: int | None, block: str | None = None) -> None:
        if isinstance(flag, dict):
            flag = flag.get("code")
        if code := _flag_code(flag):
            items.add((page, block, code))
            codes.add(code)
            if page is not None:
                pages.add(page)

    for page in canonical.get("pages") or []:
        number = canonical_page_number(page)
        for flag in [*(page.get("review_flags") or []), *(page.get("review") or [])]:
            add_flag(flag, number)
        for block in page["blocks"]:
            for flag in block.get("review_flags") or []:
                add_flag(flag, number, block.get("block_id"))

    queue_path = out_dir / "review_queue.jsonl"
    queue = []
    if queue_path.is_file():
        with queue_path.open(encoding="utf-8") as stream:
            queue = [json.loads(line) for line in stream if line.strip()]
    for source in (report, ingestion):
        for field in ("review", "review_items"):
            if isinstance(source.get(field), list):
                queue.extend(source[field])
    for item in queue:
        if isinstance(item, dict):
            page = _positive_int(item.get("page") or item.get("page_id") or item.get("page_number"))
            for flag in item.get("flags") or [item]:
                add_flag(flag, page, item.get("block_id"))

    quality = ingestion.get("quality") or report.get("ingestion_quality") or {}
    codes.update(code for key in quality.get("review_codes", {}) if (code := _flag_code(key)))
    pages.update(
        page for value in quality.get("review_pages", []) if (page := _positive_int(value))
    )
    checks = ingestion.get("checks") or report.get("ingestion_checks") or {}
    for key, check in checks.items():
        passed = check.get("pass") if isinstance(check, dict) else check
        if passed is False:
            add_flag(key.upper(), None)
    return {
        "review_item_count": max(
            len(items),
            _positive_int(report.get("review_items")) or 0,
            _positive_int(quality.get("review_items")) or 0,
        ),
        "flag_codes": sorted(codes),
        "flagged_pages": sorted(pages),
    }


def _read_outcome(out_dir: Path, result: dict, file_type: DocumentFormat) -> IdpRunOutcome:
    report = _read_report(out_dir / "report.json")
    ingestion = _read_report(out_dir / "ingestion_report.json") or result.get("ingestion") or {}
    report = {**result, **(result.get("report") or {}), **report}
    auto_publish = report.get("ingestion_auto_publish_pass", ingestion.get("auto_publish_pass"))
    assisted = report.get("ingestion_review_assisted_pass", ingestion.get("review_assisted_pass"))
    if auto_publish is not True and assisted is not True:
        return IdpRunOutcome(
            "FAILED",
            error_code="IDP_QUALITY_FAILED",
            error_message="Tài liệu chưa đạt yêu cầu xử lý. Vui lòng kiểm tra và thử lại.",
        )

    raw = (out_dir / "canonical.json").read_bytes()
    canonical = json.loads(raw.decode("utf-8"))
    if not isinstance(canonical, dict):
        raise TypeError
    schema = canonical.get("schema_version")
    if schema not in CANONICAL_SCHEMAS or not project_canonical(canonical, file_type).pages:
        raise ValueError
    return IdpRunOutcome(
        "SUCCESS" if auto_publish is True else "NEEDS_REVIEW",
        canonical_bytes=raw,
        canonical_schema_version=schema,
        page_count=len(canonical.get("pages") or []) or _positive_int(report.get("pages")),
        review_summary=None
        if auto_publish is True
        else _review_summary(canonical, report, ingestion, out_dir),
    )


def run_idp_for_version(
    source_path: Path, work_out_dir: Path, *, config: Settings, file_type: DocumentFormat
) -> IdpRunOutcome:
    try:
        _ensure_idp_importable(config)
        from idp.input.filetypes import InputError, InputLimits

        try:
            suffixes = {SOURCE_SUFFIX[file_type]}
            if file_type == "MARKDOWN":
                suffixes.add(".markdown")
            if source_path.suffix.lower() not in suffixes:
                raise InputError("UNSUPPORTED_FILE_TYPE", "Unexpected source extension")
            from idp.ingest import ingest_file

            result = ingest_file(
                source_path,
                work_out_dir,
                limits=InputLimits(
                    max_bytes=config.document_max_size_bytes, max_pages=config.document_max_pages
                ),
                language="vie",
            )
            return _read_outcome(work_out_dir, result, file_type)
        except InputError as exc:
            return IdpRunOutcome(
                "FAILED",
                error_code=_flag_code(exc.code) or "IDP_PROCESSING_ERROR",
                error_message="Không thể đọc tài liệu. Vui lòng kiểm tra tệp và thử lại.",
            )
    except ImportError:
        return IdpRunOutcome(
            "FAILED", error_code="IDP_UNAVAILABLE", error_message=PROCESSING_ERROR_MESSAGE
        )
    except Exception:  # noqa: BLE001 - IDP parser and artifact failures stay within the job.
        return IdpRunOutcome(
            "FAILED", error_code="IDP_PROCESSING_ERROR", error_message=PROCESSING_ERROR_MESSAGE
        )
