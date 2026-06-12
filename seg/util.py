"""Small shared helpers."""
from __future__ import annotations
import json


class NoValidData(ValueError):
    """Raised when a dataset is empty / has no usable rows after cleaning."""


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
