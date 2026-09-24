"""Structured JSON logging with correlation IDs (PDF Standards: 'Structured logging with
correlation IDs. Print statements do not count.').

Every log line carries ``run_id`` (one pipeline/sweep run) and ``correlation_id`` (one fund x
window check), so all HTTP calls, detection decisions and DB writes for one check can be
traced together.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.correlation_id = correlation_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", run_id_var.get()),
            "correlation_id": getattr(
                record, "correlation_id", correlation_id_var.get()
            ),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter and context filter on the root logger (idempotent)."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h.formatter, JsonFormatter):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.setLevel(level)


@contextlib.contextmanager
def correlation_scope(
    correlation_id: str | None = None, run_id: str | None = None
) -> Iterator[str]:
    """Set run/correlation IDs for everything logged inside the block."""
    cid = correlation_id or new_id("chk")
    tok_c = correlation_id_var.set(cid)
    tok_r = run_id_var.set(run_id) if run_id else None
    try:
        yield cid
    finally:
        correlation_id_var.reset(tok_c)
        if tok_r is not None:
            run_id_var.reset(tok_r)
