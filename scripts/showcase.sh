#!/usr/bin/env bash
# Запуск интерактивного дашборда для презентации и защиты PII Gateway
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

echo "======================================================="
echo " Запуск PII Gateway Interactive Showcase"
echo " Откроется http://127.0.0.1:8790"
echo "======================================================="

PYTHONPATH="src" "$PYTHON" -m pii_gateway.web "$@"
