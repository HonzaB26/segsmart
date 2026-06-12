"""Small shared helpers."""
from __future__ import annotations
import json
import os
import tempfile


class NoValidData(ValueError):
    """Raised when a dataset is empty / has no usable rows after cleaning."""


def atomic_write_json(path: str, obj, indent=2) -> str:
    """Write JSON via temp-file + os.replace so concurrent readers (the
    ThreadingHTTPServer serves these files) never see a partial body."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def extract_json(raw: str) -> dict:
    """Pull the first JSON object out of model output.

    Handles ```json fences, prose around the object, and reasoning-model
    chatter, using JSONDecoder.raw_decode from each '{' candidate.
    """
    if not raw or not raw.strip():
        raise ValueError("empty model output")
    dec = json.JSONDecoder()
    s = raw
    # strip a leading ```json fence if present (cheap fast-path)
    start = 0
    while True:
        i = s.find("{", start)
        if i == -1:
            raise ValueError("no JSON object found")
        try:
            obj, _ = dec.raw_decode(s[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = i + 1
