#!/bin/sh
# Dashboard works immediately from the baked result.json. The local LLM is only
# needed to (re)generate campaign copy live — pull it on demand via SEG_AUTOPULL=1.
set -e

if [ "$SEG_AUTOPULL" = "1" ]; then
  echo "[segsmart] waiting for Ollama at $OLLAMA_URL ..."
  until curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; do sleep 2; done
  echo "[segsmart] ensuring model '$SEG_LLM_MODEL' is present (this can be a large first pull) ..."
  curl -s "$OLLAMA_URL/api/pull" -d "{\"name\":\"$SEG_LLM_MODEL\"}" >/dev/null || \
    echo "[segsmart] model pull failed — dashboard still works from baked results"
fi

echo "[segsmart] dashboard → http://localhost:${SEG_PORT}"
exec python3.11 server.py
