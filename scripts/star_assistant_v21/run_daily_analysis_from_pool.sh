#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
ANALYZER_CONTAINER="${ANALYZER_CONTAINER:-stock-analyzer}"
ANALYSIS_WORKERS="${ANALYSIS_WORKERS:-1}"
SEND_NOTIFY="${SEND_NOTIFY:-false}"
RUN_MARKET_REVIEW="${RUN_MARKET_REVIEW:-false}"

STOCK_LIST_PATH="$PROJECT_DIR/data/fundamental_pool/current_stock_list.txt"
if [[ ! -f "$STOCK_LIST_PATH" ]]; then
  echo "[daily-analysis] missing $STOCK_LIST_PATH" >&2
  exit 1
fi

STOCK_LIST="$(tr -d "[:space:]" < "$STOCK_LIST_PATH")"
if [[ -z "$STOCK_LIST" ]]; then
  echo "[daily-analysis] empty STOCK_LIST" >&2
  exit 1
fi

NOTIFY_ARG="--no-notify"
if [[ "$SEND_NOTIFY" == "true" ]]; then
  NOTIFY_ARG=""
fi
MARKET_REVIEW_ARG="--no-market-review"
if [[ "$RUN_MARKET_REVIEW" == "true" ]]; then
  MARKET_REVIEW_ARG=""
fi

echo "[daily-analysis] STOCK_LIST=$STOCK_LIST"
sudo docker exec \
  -e ENV_FILE=/app/data/app.env \
  -e SCHEDULE_ENABLED=false \
  -e WEBUI_ENABLED=false \
  -e RUN_IMMEDIATELY=true \
  -e STOCK_LIST_OVERRIDE="$STOCK_LIST" \
  "$ANALYZER_CONTAINER" \
  sh -lc "cd /app && PYTHONPATH=/app python main.py --stocks \"$STOCK_LIST\" --workers $ANALYSIS_WORKERS --force-run $NOTIFY_ARG $MARKET_REVIEW_ARG"
