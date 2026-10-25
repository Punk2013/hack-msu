#!/usr/bin/env bash
# Demo: masking gateway + demo_agent + local Ollama, synthetic data only.
#   scripts/demo.sh [model] ["task"]
# Artifacts (vault, dumps, report.html) go to demo/.run/, outputs to demo/out/.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL=${1:-qwen2.5:7b}
TASK=${2:-"Прочитай карточки в clients/ и для каждого клиента напиши уведомление в out/ с тем же именем файла. Обратись по имени и отчеству и укажи контакты из карточки."}
RUN=demo/.run/$(date +%H%M%S)
mkdir -p "$RUN" demo/out
export PII_VAULT_DIR=$PWD/$RUN/vault PII_PROJECT_ID=demo PII_DUMP_DIR=$PWD/$RUN/dumps
PII_UPSTREAM_BASE_URL=${PII_UPSTREAM_BASE_URL:-http://localhost:11434/v1} \
PII_WORKSPACE=$PWD/demo PII_WRITE_ROOTS=$PWD/demo/out \
  .venv/bin/uvicorn pii_gateway.gateway.app:app --host 127.0.0.1 --port 8787 \
  >"$RUN/gateway.log" 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null' EXIT
for _ in $(seq 80); do curl -s -m 1 127.0.0.1:8787/v1/models >/dev/null && break; sleep 0.25; done
.venv/bin/python -m pii_gateway.demo_agent --stream --model "$MODEL" --workspace demo "$TASK"
.venv/bin/python -m pii_gateway.report --dumps "$RUN/dumps" --project demo --out "$RUN/report.html"
echo "Отчёт: $RUN/report.html"
