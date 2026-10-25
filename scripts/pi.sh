#!/usr/bin/env bash
# Run the pi coding agent (https://github.com/badlogic/pi-mono) through the PII gateway.
#
#   scripts/pi.sh [-p local|openai|openrouter|URL] [-m MODEL] [-w WORKSPACE] [-k PROJECT] [pi args...]
#
#   -p  provider behind the gateway: local (Ollama, default), openai, openrouter or a base URL
#   -m  model id (default: qwen2.5:7b / gpt-4o-mini / openai/gpt-4o-mini)
#   -w  folder pi works in (default: ~/pii-run/<project>/workspace with the demo cards)
#   -k  vault project id (default: pi)
#   PORT=8788 scripts/pi.sh ...  if 8787 is busy
#   pi arguments go after --, e.g. scripts/pi.sh -- -p "one-shot prompt"
#
# pi talks only to the local gateway; the provider key stays in PII_UPSTREAM_API_KEY of the
# gateway. pi runs with its own config dir and without the bash tool: shell output is not
# restored and can reach the network. Artifacts: ~/pii-run/<project>/. Synthetic data only.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
REPO=$PWD
PORT=${PORT:-8787}

PROVIDER=local MODEL="" WORKSPACE="" PROJECT=pi
while getopts "p:m:w:k:h" opt; do
  case $opt in
    p) PROVIDER=$OPTARG ;; m) MODEL=$OPTARG ;; w) WORKSPACE=$OPTARG ;; k) PROJECT=$OPTARG ;;
    *) sed -n '2,15p' "$0"; exit 0 ;;
  esac
done
shift $((OPTIND - 1))

case $PROVIDER in
  local) BASE=http://localhost:11434/v1; MODEL=${MODEL:-qwen2.5:7b} ;;
  openai) BASE=https://api.openai.com/v1; MODEL=${MODEL:-gpt-4o-mini} ;;
  openrouter) BASE=https://openrouter.ai/api/v1; MODEL=${MODEL:-openai/gpt-4o-mini} ;;
  http*) BASE=$PROVIDER; [ -n "$MODEL" ] || { echo "укажите -m для своего URL" >&2; exit 1; } ;;
  *) echo "неизвестный провайдер: $PROVIDER" >&2; exit 1 ;;
esac
if [ "$PROVIDER" != local ] && [ -z "${PII_UPSTREAM_API_KEY:-}" ]; then
  read -rsp "API-ключ провайдера (не сохраняется): " PII_UPSTREAM_API_KEY; echo
  export PII_UPSTREAM_API_KEY
fi
PI=$(command -v pi || true)
[ -n "$PI" ] || { echo "pi не найден: npm install -g @mariozechner/pi-coding-agent" >&2; exit 1; }

RUN=$HOME/pii-run/$PROJECT
mkdir -p "$RUN"
if [ -z "$WORKSPACE" ]; then
  WORKSPACE=$RUN/workspace
  mkdir -p "$WORKSPACE"
  cp -n demo/clients/*.txt "$WORKSPACE/" 2>/dev/null || true
fi
WORKSPACE=$(realpath "$WORKSPACE")

export PII_VAULT_DIR=$RUN/vault PII_DUMP_DIR=$RUN/dumps PII_PROJECT_ID=$PROJECT
TOKEN=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
PII_LOCAL_TOKEN=$TOKEN PII_UPSTREAM_BASE_URL=$BASE PII_WORKSPACE=$WORKSPACE \
PII_WRITE_ROOTS=$WORKSPACE "$REPO/.venv/bin/uvicorn" pii_gateway.gateway.app:app \
  --host 127.0.0.1 --port $PORT >"$RUN/gateway.log" 2>&1 &
GW=$!
report() {
  "$REPO/.venv/bin/python" -m pii_gateway.report --dumps "$RUN/dumps" --project "$PROJECT" \
    --out "$RUN/report.html" >/dev/null 2>&1 && echo "отчёт: $RUN/report.html"
  kill $GW 2>/dev/null
}
trap report EXIT
for _ in $(seq 80); do
  kill -0 "$GW" 2>/dev/null || { echo "gateway упал, лог: $RUN/gateway.log" >&2; exit 1; }
  curl --fail -s -m 1 -H "Authorization: Bearer $TOKEN" 127.0.0.1:$PORT/v1/models >/dev/null && break
  sleep 0.25
done

# pi config of its own: a single provider "pii-gateway", nothing from ~/.pi is used
export PI_CODING_AGENT_DIR=$RUN/pi-agent PI_OFFLINE=1 PI_TELEMETRY=0
mkdir -p "$PI_CODING_AGENT_DIR"
cat >"$PI_CODING_AGENT_DIR/models.json" <<EOF
{
  "providers": {
    "pii-gateway": {
      "baseUrl": "http://127.0.0.1:$PORT/v1",
      "api": "openai-completions",
      "apiKey": "$TOKEN",
      "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false},
      "models": [{"id": "$MODEL", "name": "$MODEL via PII gateway"}]
    }
  }
}
EOF
echo "pi → gateway → $BASE ($MODEL), папка $WORKSPACE, артефакты $RUN"
cd "$WORKSPACE"
"$PI" --provider pii-gateway --model "$MODEL" --tools read,ls,grep,find,write,edit "$@"
