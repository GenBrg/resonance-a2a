"""Structured logging with defence-in-depth redaction.

The platform handles私密 intent, so log lines are treated as a leak surface: a
filter scrubs anything that looks like a credential or a private payload before
the record is formatted, no matter which module logged it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

_SENSITIVE_KEYS = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|vault|hidden_intent|bottleneck|contrarian|"
    r"deal_breaker|blocklist|transcript|pitch|email)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE)


def scrub(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[depth]"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) else scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value][:50]
    if isinstance(value, str):
        return _BEARER.sub(r"\1[REDACTED]", value)[:2000]
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.args, dict):
            record.args = scrub(record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage() if not isinstance(record.msg, dict) else "",
            "request_id": request_id_var.get(),
            "user": user_id_var.get(),
        }
        if isinstance(record.msg, dict):
            payload["event"] = record.msg
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(scrub(payload), ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    COLORS = {"DEBUG": "\033[90m", "INFO": "\033[36m", "WARNING": "\033[33m", "ERROR": "\033[31m"}

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        body = record.getMessage()
        if isinstance(record.msg, dict):
            body = json.dumps(scrub(record.msg), ensure_ascii=False)
        rid = request_id_var.get()
        prefix = f"{color}{record.levelname:<7}\033[0m {record.name:<28} "
        if rid != "-":
            prefix += f"\033[90m[{rid[:8]}]\033[0m "
        text = f"{prefix}{body}"
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else PrettyFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel("WARNING")


def log(event: str, **fields: Any) -> None:
    logging.getLogger("resonance").info({"event": event, **fields})


def warn(event: str, **fields: Any) -> None:
    logging.getLogger("resonance").warning({"event": event, **fields})
