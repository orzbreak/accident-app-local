"""Persist model response text for local troubleshooting."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

LOG_FILE = Path(__file__).resolve().parents[1] / "logs" / "api-responses.jsonl"
_lock = Lock()


def record(stage, **fields):
    if os.getenv("ACCIDENT_LOG_MODEL_RESPONSES", "1") != "1":
        return
    entry = {"time": datetime.now(timezone.utc).isoformat(), "stage": stage, **fields}
    try:
        with _lock:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Could not write local API response log")
