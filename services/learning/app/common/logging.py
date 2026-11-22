import logging

logger = logging.getLogger("app.learning")

SAFE_EVENTS = frozenset(
    {
        "service_started",
        "service_stopped",
        "startup_failed",
        "request_failed",
        "dependency_unready",
        "sweeper_cycle_completed",
        "sweeper_cycle_failed",
        "outbox_dispatch_completed",
        "outbox_dispatch_failed",
    }
)


class RedactionFilter(logging.Filter):
    """Only emit fixed operational event names; never log user answers, tokens, or stack details."""

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
            for name in ("app.learning", "uvicorn", "uvicorn.error", "uvicorn.access")
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    }
