#!/usr/bin/env bash
# Demo: masking gateway + demo_agent + local Ollama, synthetic data only.
#   scripts/demo.sh [model] ["task"]
#   SANDBOX=1 scripts/demo.sh ...   agent runs in bubblewrap, network only to the gateway
# A fresh temporary run keeps vault/dumps outside the agent's copied workspace.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
MODEL=${1:-qwen2.5:7b}
TASK=${2:-"Для каждого файла в clients/: сначала прочитай его через read_file, затем запиши клиенту уведомление в out/ с тем же именем файла. Обратись по имени и отчеству. Используй только данные из карточки, ничего не придумывай."}
RUN=$(mktemp -d "${TMPDIR:-/tmp}/pii-gateway-demo.XXXXXX")
WORKSPACE=$RUN/workspace
mkdir -p "$WORKSPACE/clients" "$WORKSPACE/out"
cp demo/clients/*.txt "$WORKSPACE/clients/"
export PII_VAULT_DIR=$RUN/vault PII_PROJECT_ID=demo PII_DUMP_DIR=$RUN/dumps
export PII_LOCAL_TOKEN=${PII_LOCAL_TOKEN:-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}
PII_UPSTREAM_BASE_URL=${PII_UPSTREAM_BASE_URL:-http://localhost:11434/v1} \
PII_WORKSPACE=$WORKSPACE PII_WRITE_ROOTS=$WORKSPACE/out \
  .venv/bin/uvicorn pii_gateway.gateway.app:app --host 127.0.0.1 --port 8787 \
  >"$RUN/gateway.log" 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null' EXIT
ready=0
for _ in $(seq 80); do
  kill -0 "$GW" 2>/dev/null || break
  if curl --fail -s -m 1 -H "Authorization: Bearer $PII_LOCAL_TOKEN" \
    127.0.0.1:8787/v1/models >/dev/null; then ready=1; break; fi
  sleep 0.25
done
if [ "$ready" != 1 ]; then echo "Gateway не готов; лог: $RUN/gateway.log" >&2; exit 1; fi
AGENT_ARGS=(--stream --base-url http://127.0.0.1:8787/v1 --model "$MODEL" --workspace "$WORKSPACE")
if [ "${SANDBOX:-0}" = 1 ]; then
  .venv/bin/python -m pii_gateway.sandbox --workspace "$WORKSPACE" --write-root "$WORKSPACE/out" \
    --runtime-root "$PWD/.venv" --protect "$RUN/vault" --protect "$RUN/dumps" --demo-agent \
    -- "$PWD/.venv/bin/python" "${AGENT_ARGS[@]}" "$TASK"
else
  .venv/bin/python -m pii_gateway.demo_agent "${AGENT_ARGS[@]}" "$TASK"
fi
.venv/bin/python -m pii_gateway.report --dumps "$RUN/dumps" --project demo --out "$RUN/report.html"
echo "Отчёт: $RUN/report.html"
echo "Итоговые файлы: $WORKSPACE/out"
