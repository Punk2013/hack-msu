#!/usr/bin/env bash
# Demo: masking gateway + demo_agent + local Ollama, synthetic data only.
#   scripts/demo.sh [model] ["task"]
# A fresh temporary run keeps vault/dumps outside the agent's copied workspace.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
MODEL=${1:-qwen2.5:7b}
TASK=${2:-"Прочитай карточки в clients/ и для каждого клиента напиши уведомление в out/ с тем же именем файла. Обратись по имени и отчеству и укажи контакты из карточки."}
RUN=$(mktemp -d "${TMPDIR:-/tmp}/pii-gateway-demo.XXXXXX")
WORKSPACE=$RUN/workspace
mkdir -p "$WORKSPACE/clients" "$WORKSPACE/out"
cp demo/clients/*.txt "$WORKSPACE/clients/"
export PII_VAULT_DIR=$RUN/vault PII_PROJECT_ID=demo PII_DUMP_DIR=$RUN/dumps
PII_UPSTREAM_BASE_URL=${PII_UPSTREAM_BASE_URL:-http://localhost:11434/v1} \
PII_WORKSPACE=$WORKSPACE PII_WRITE_ROOTS=$WORKSPACE/out \
  .venv/bin/uvicorn pii_gateway.gateway.app:app --host 127.0.0.1 --port 8787 \
  >"$RUN/gateway.log" 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null' EXIT
ready=0
for _ in $(seq 80); do
  kill -0 "$GW" 2>/dev/null || break
  if curl --fail -s -m 1 127.0.0.1:8787/v1/models >/dev/null; then ready=1; break; fi
  sleep 0.25
done
if [ "$ready" != 1 ]; then echo "Gateway не готов; лог: $RUN/gateway.log" >&2; exit 1; fi
.venv/bin/python -m pii_gateway.demo_agent --stream --model "$MODEL" --workspace "$WORKSPACE" "$TASK"
.venv/bin/python -m pii_gateway.report --dumps "$RUN/dumps" --project demo --out "$RUN/report.html"
echo "Отчёт: $RUN/report.html"
echo "Итоговые файлы: $WORKSPACE/out"
