#!/usr/bin/env bash
# Run the pi coding agent through PII Gateway: the model only ever sees fake
# personal data, files on disk keep the real data. Use pi as usual.
#
#   scripts/pi.sh                      # pick a model from the menu
#   scripts/pi.sh ollama:qwen2.5:7b    # local model via Ollama
#   scripts/pi.sh openrouter:qwen/qwen-2.5-72b-instruct
#   scripts/pi.sh openai:gpt-4o-mini -- -p "Сделай сводку по clients/"   # extra pi args after --
#
# Cloud keys come from OPENROUTER_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY or are
# asked once; only the gateway sees them, pi gets a local token. bash is disabled in pi.
# After pi exits the report (journal + report.html) is in ~/pii-gateway-reports/<time>/.
set -euo pipefail
umask 077
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORKDIR=$PWD

choice=${1:-}
[ "${1:-}" ] && shift
[ "${1:-}" = "--" ] && shift

if [ -z "$choice" ]; then
  echo "Модель для pi (через PII Gateway):"
  mapfile -t local_models < <(ollama list 2>/dev/null | awk 'NR>1 {print $1}')
  options=()
  for m in "${local_models[@]}"; do options+=("ollama:$m"); done
  options+=("openrouter:qwen/qwen-2.5-72b-instruct" "openai:gpt-4o-mini" "deepseek:deepseek-chat"
            "custom")
  select opt in "${options[@]}"; do [ -n "$opt" ] && choice=$opt && break; done
fi

provider=${choice%%:*}
model=${choice#*:}
case $provider in
  ollama)     base=http://localhost:11434/v1; key_var=; ;;
  openrouter) base=https://openrouter.ai/api/v1; key_var=OPENROUTER_API_KEY ;;
  openai)     base=https://api.openai.com/v1; key_var=OPENAI_API_KEY ;;
  deepseek)   base=https://api.deepseek.com/v1; key_var=DEEPSEEK_API_KEY ;;
  custom)
    read -rp "Base URL (OpenAI-совместимый, …/v1): " base
    read -rp "Модель: " model
    key_var=PII_UPSTREAM_API_KEY ;;
  *) echo "неизвестный провайдер: $provider (ollama|openrouter|openai|deepseek|custom)" >&2; exit 2 ;;
esac
key=
if [ -n "$key_var" ]; then
  key=${!key_var:-}
  [ -z "$key" ] && read -rsp "API-ключ ($key_var), увидит только gateway: " key && echo
  [ -z "$key" ] && { echo "нужен API-ключ" >&2; exit 2; }
fi

PI_BIN=$(command -v pi || true)
[ -z "$PI_BIN" ] && [ -x "$HOME/.local/share/pi-agent/node_modules/.bin/pi" ] \
  && PI_BIN=$HOME/.local/share/pi-agent/node_modules/.bin/pi
[ -z "$PI_BIN" ] && { echo "pi не найден: npm install -g @mariozechner/pi-coding-agent" >&2; exit 2; }

REPORT=$HOME/pii-gateway-reports/$(date +%Y%m%d-%H%M%S)
mkdir -p "$REPORT"
PORT=$("$REPO/.venv/bin/python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
TOKEN=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
PROJECT=pi-$(printf '%s' "$WORKDIR" | sha256sum | cut -c1-12)

PII_UPSTREAM_BASE_URL=$base PII_UPSTREAM_API_KEY=$key PII_LOCAL_TOKEN=$TOKEN \
PII_PROJECT_ID=$PROJECT PII_WORKSPACE=$WORKDIR PII_WRITE_ROOTS=$WORKDIR \
PII_JOURNAL=$REPORT/journal.jsonl \
  "$REPO/.venv/bin/uvicorn" pii_gateway.gateway.app:app --host 127.0.0.1 --port "$PORT" \
  --log-level warning >"$REPORT/gateway.log" 2>&1 &
GW=$!
unset key

finish() {
  kill "$GW" 2>/dev/null || true
  if [ -s "$REPORT/journal.jsonl" ]; then
    "$REPO/.venv/bin/python" -m pii_gateway.journal_report \
      "$REPORT/journal.jsonl" "$REPORT/report.html" >/dev/null
    echo "Отчёт PII Gateway: $REPORT/report.html"
  else
    echo "Обращений к модели не было; лог gateway: $REPORT/gateway.log"
  fi
}
trap finish EXIT

for _ in $(seq 80); do
  kill -0 "$GW" 2>/dev/null || { echo "gateway не запустился: $REPORT/gateway.log" >&2; exit 1; }
  curl --fail -s -m 1 -H "Authorization: Bearer $TOKEN" "127.0.0.1:$PORT/v1/models" >/dev/null && break
  sleep 0.25
done

# Separate pi config dir: only our provider, no other endpoints, no telemetry/update calls.
export PI_CODING_AGENT_DIR=$HOME/.local/share/pii-gateway/pi-agent
mkdir -p "$PI_CODING_AGENT_DIR"
cat >"$PI_CODING_AGENT_DIR/models.json" <<JSON
{
  "providers": {
    "pii-gateway": {
      "baseUrl": "http://127.0.0.1:$PORT/v1",
      "api": "openai-completions",
      "apiKey": "$TOKEN",
      "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false},
      "models": [{"id": "$model", "name": "$model (через PII Gateway)"}]
    }
  }
}
JSON
export PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0

"$PI_BIN" --provider pii-gateway --model "$model" --tools read,edit,write,grep,find,ls "$@"
