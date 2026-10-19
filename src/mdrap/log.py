"""MDRAP Structured Logging and Trace Context (Item 3.5).

Provides high-throughput structured JSON logging, standard text formatting,
and thread/coroutine-local trace context propagation for observability.
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import json
import logging
import sys
import traceback
from typing import Any, Optional

__stability__ = "stable"

# Context variables for distributed / event-level tracing
_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "span_id", default=None
)


def set_trace_context(trace_id: Optional[str], span_id: Optional[str] = None) -> None:
    """Set active trace and span identifiers for the current task/thread."""
    _current_trace_id.set(trace_id)
    _current_span_id.set(span_id)


def get_trace_context() -> tuple[Optional[str], Optional[str]]:
    """Return (trace_id, span_id) currently active in the execution context."""
    return _current_trace_id.get(), _current_span_id.get()


class JSONLogFormatter(logging.Formatter):
    """Institutional single-line JSON log formatter with trace context support."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", None) or _current_trace_id.get()
        span_id = getattr(record, "span_id", None) or _current_span_id.get()

        created_dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        iso_ts = created_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        entry: dict[str, Any] = {
            "timestamp": iso_ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "lineno": record.lineno,
        }

        if trace_id is not None:
            entry["trace_id"] = trace_id
        if span_id is not None:
            entry["span_id"] = span_id

        if record.exc_info:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(entry, default=str)


class TextLogFormatter(logging.Formatter):
    """Clean standard text log formatter with optional trace context."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", None) or _current_trace_id.get()
        prefix = f"[{trace_id}] " if trace_id else ""
        orig = super().format(record)
        return f"{prefix}{orig}"


def configure_logging(
    level: str = "INFO",
    log_format: str = "text",
    stream: Optional[Any] = None,
) -> None:
    """Configure MDRAP logging with text or structured JSON output.

    Args:
        level: Minimum log level string ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        log_format: Output format ('text' or 'json').
        stream: Target stream for logs (defaults to sys.stderr).
    """
    target_stream = stream or sys.stderr
    lvl_str = str(level).upper() if isinstance(level, str) else "INFO"
    fmt_str = str(log_format).lower() if isinstance(log_format, str) else "text"
    numeric_level = getattr(logging, lvl_str, logging.INFO)

    handler = logging.StreamHandler(target_stream)
    if fmt_str == "json":
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(
            TextLogFormatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    # Remove existing MDRAP stream handlers to prevent duplicates
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)

    root.addHandler(handler)
    root.setLevel(numeric_level)
