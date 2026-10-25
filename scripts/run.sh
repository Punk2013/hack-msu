#!/usr/bin/env bash
# Run the PII gateway and the demo agent with your own prompts.
#
#   scripts/run.sh [-p local|openai|openrouter|URL] [-m MODEL] [-w WORKSPACE] [-k PROJECT] ["prompt"]
#
#   -p  provider: local (Ollama, default), openai, openrouter, or any OpenAI-compatible base URL
#   -m  model (default: qwen2.5:7b for local, gpt-4o-mini for openai, openai/gpt-4o-mini for openrouter)
#   -w  workspace the agent may read; results go to WORKSPACE/out (default: a copy of demo/clients)
#   -k  vault project id (default: my); the same person gets the same pseudonym across runs
#
# Cloud providers need the key in PII_UPSTREAM_API_KEY; it stays with the gateway, the agent never sees it.
# Without a prompt the script asks for prompts in a loop (empty line or Ctrl-D to quit).
# Artifacts: ~/pii-run/<project>/{vault,dumps,report.html,gateway.log}. Synthetic data only.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

PROVIDER=local MODEL="" WORKSPACE="" PROJECT=my
while getopts "p:m:w:k:h" opt; do
  case $opt in
    p) PROVIDER=$OPTARG ;; m) MODEL=$OPTARG ;; w) WORKSPACE=$OPTARG ;; k) PROJECT=$OPTARG ;;
    *) sed -n '2,13p' "$0"; exit 0 ;;
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

RUN=$HOME/pii-run/$PROJECT
mkdir -p "$RUN"
if [ -z "$WORKSPACE" ]; then
  WORKSPACE=$RUN/workspace
  mkdir -p "$WORKSPACE"
  cp -n demo/clients/*.txt "$WORKSPACE/" 2>/dev/null || true
fi
WORKSPACE=$(realpath "$WORKSPACE")
mkdir -p "$WORKSPACE/out"

export PII_VAULT_DIR=$RUN/vault PII_DUMP_DIR=$RUN/dumps PII_PROJECT_ID=$PROJECT
export PII_LOCAL_TOKEN=${PII_LOCAL_TOKEN:-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}
PII_UPSTREAM_BASE_URL=$BASE PII_WORKSPACE=$WORKSPACE PII_WRITE_ROOTS=$WORKSPACE/out \
  .venv/bin/uvicorn pii_gateway.gateway.app:app --host 127.0.0.1 --port 8787 >"$RUN/gateway.log" 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null' EXIT
for _ in $(seq 80); do
  kill -0 "$GW" 2>/dev/null || { echo "gateway упал, лог: $RUN/gateway.log" >&2; exit 1; }
  curl --fail -s -m 1 -H "Authorization: Bearer $PII_LOCAL_TOKEN" 127.0.0.1:8787/v1/models >/dev/null && break
  sleep 0.25
done
echo "gateway → $BASE, модель $MODEL, workspace $WORKSPACE"

ask() {
  .venv/bin/python -m pii_gateway.demo_agent --stream --base-url http://127.0.0.1:8787/v1 \
    --model "$MODEL" --workspace "$WORKSPACE" "$1" || echo "(агент завершился с ошибкой)" >&2
  .venv/bin/python -m pii_gateway.report --dumps "$RUN/dumps" --project "$PROJECT" \
    --out "$RUN/report.html" >/dev/null 2>&1 || true
  echo; echo "── результат: $WORKSPACE/out   отчёт: $RUN/report.html"
}

if [ $# -gt 0 ]; then
  ask "$*"
else
  while IFS= read -rep "промпт> " PROMPT && [ -n "$PROMPT" ]; do ask "$PROMPT"; done
fi
