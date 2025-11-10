import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional


def _init_logger() -> logging.Logger:
    logger = logging.getLogger("workflow_builder")
    if not logger.handlers:
        handler = logging.StreamHandler()
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level, logging.INFO))
        handler.setLevel(getattr(logging, level, logging.INFO))
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    return logger


_LOGGER = _init_logger()


def log_event(category: str, action: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Emit a structured JSON log line for observability.

    Args:
        category: High-level area (e.g., 'server', 'agent', 'llm').
        action: Specific action or phase (e.g., 'route_start', 'decision').
        data: Extra key-value pairs to attach.
    """
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "category": category,
        "action": action,
        "data": data or {},
    }
    try:
        _LOGGER.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Fallback to plain logging if JSON fails
        _LOGGER.info(f"{category} {action} {data}")