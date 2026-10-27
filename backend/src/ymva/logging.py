"""Structured logging: JSON in production, readable console output in development."""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from loguru import logger as pipecat_logger

from ymva.config import get_settings

# Phone numbers: 10-13 digits, optional leading "+", not part of a longer token.
_PHONE_RE = re.compile(r"(?<![\w+])\+?\d{10,13}(?!\w)")
# Telephony webhook addresses carry a secret (/api/telephony/plivo/<account>/<secret>/answer)
# and media streams a one-time token (/api/telephony/plivo/ws/<token>).
_WEBHOOK_SECRET_RE = re.compile(r"(/api/telephony/[^/\s]+/[^/\s]+/)[^/\s?\"']+")
_SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "auth_token", "authorization"}


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _WEBHOOK_SECRET_RE.sub(r"\1***", value)
        return _PHONE_RE.sub(lambda m: "***" + m.group()[-4:], value)
    return value


class _MaskFilter(logging.Filter):
    """Same masking for standard-library loggers (uvicorn logs websocket paths)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _mask_value(record.getMessage())
        record.args = None
        return True


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Never log secrets; mask phone numbers (customer PII)."""
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            event_dict[key] = "[redacted]"
        else:
            event_dict[key] = _mask_value(event_dict[key])
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, format="%(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_MaskFilter())
    # Pipecat logs through loguru, which defaults to DEBUG: that prints each call's system
    # instruction (customer details) and what callers say. Same level as ours, phones masked.
    pipecat_logger.remove()
    pipecat_logger.add(
        sys.stderr,
        level=logging.getLevelName(level),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} - {message}",
        filter=_mask_record,
    )


def _mask_record(record: Any) -> bool:
    record["message"] = _mask_value(record["message"])
    return True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
