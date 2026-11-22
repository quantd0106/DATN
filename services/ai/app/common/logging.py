import logging

logger = logging.getLogger("app.ai")

SAFE_EVENTS = frozenset(
    {
        "service_started",
        "service_stopped",
        "startup_failed",
        "request_failed",
        "dependency_unready",
        "worker_cycle_completed",
        "worker_cycle_failed",
        "stale_state_recovered",
        "quiz_export_retried",
        "quiz_job_recovered",
    }
)


class RedactionFilter(logging.Filter):
    """Only publish fixed operational events. Arbitrary text can include chat or secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error" and record.levelno >= logging.ERROR:
            record.msg = "server_error"
        elif not (
            record.name == logger.name and isinstance(record.msg, str) and record.msg in SAFE_EVENTS
        ):
            return False
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def server_log_config() -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"redact": {"()": RedactionFilter}},
        "formatters": {"safe": {"format": "%(levelname)s %(message)s"}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "filters": ["redact"],
                "formatter": "safe",
            }
        },
        "loggers": {
            name: {"handlers": [], "propagate": True, "level": "INFO"}
            for name in ("app.ai", "uvicorn", "uvicorn.error", "uvicorn.access")
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    }
