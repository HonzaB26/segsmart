# SegSmart — local-friendly customer segmentation. openSUSE base (on-brand,
# and the buy-once artifact the SME runs on their own metal).
FROM opensuse/leap:15.6

RUN zypper --non-interactive refresh \
 && zypper --non-interactive install python311 python311-pip curl \
 && zypper clean -a

WORKDIR /app

COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

# application code
COPY seg/ ./seg/
COPY gen/ ./gen/
COPY pipeline.py server.py index.html docker-entrypoint.sh ./
# baked demo data so the dashboard works out-of-box (real exports come via
# DB connectors or the UI upload — never baked into the shipped image)
COPY data/synthetic_milan.csv ./data/
COPY out/result.json ./out/

RUN chmod +x docker-entrypoint.sh

ENV SEG_PORT=8099 \
    OLLAMA_URL=http://ollama:11434 \
    SEG_LLM_MODEL=gemma4:e4b \
    SEG_AUTOPULL=0

EXPOSE 8099
ENTRYPOINT ["./docker-entrypoint.sh"]
