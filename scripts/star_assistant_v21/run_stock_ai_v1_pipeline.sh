#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
ANALYZER_CONTAINER="${ANALYZER_CONTAINER:-stock-analyzer}"
POOL_SIZE="${POOL_SIZE:-50}"
ANALYZE_COUNT="${ANALYZE_COUNT:-10}"
RUN_ANALYSIS="${RUN_ANALYSIS:-true}"
SEND_NOTIFY="${SEND_NOTIFY:-false}"
RUN_MARKET_REVIEW="${RUN_MARKET_REVIEW:-false}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"

echo "[pipeline] 1/9 fundamental pool"
POOL_SIZE="$POOL_SIZE" ANALYZE_COUNT="$ANALYZE_COUNT" $SCRIPT_DIR/run_fundamental_pool.sh

STOCK_LIST="$(tr -d "[:space:]" < "$PROJECT_DIR/data/fundamental_pool/current_stock_list.txt")"
if [[ -z "$STOCK_LIST" ]]; then
  echo "[pipeline] empty STOCK_LIST from fundamental pool" >&2
  exit 1
fi
echo "[pipeline] STOCK_LIST=$STOCK_LIST"

if [[ "$RUN_ANALYSIS" == "true" ]]; then
  echo "[pipeline] 2/9 daily_stock_analysis"
  NOTIFY_ARG="--no-notify"
  if [[ "$SEND_NOTIFY" == "true" ]]; then
    NOTIFY_ARG=""
  fi
  MARKET_REVIEW_ARG="--no-market-review"
  if [[ "$RUN_MARKET_REVIEW" == "true" ]]; then
    MARKET_REVIEW_ARG=""
  fi
  sudo docker exec \
    -e ENV_FILE=/app/data/app.env \
    -e SCHEDULE_ENABLED=false \
    -e WEBUI_ENABLED=false \
    -e RUN_IMMEDIATELY=true \
    -e STOCK_LIST_OVERRIDE="$STOCK_LIST" \
    "$ANALYZER_CONTAINER" \
    sh -lc "cd /app && PYTHONPATH=/app python main.py --stocks \"$STOCK_LIST\" --workers 1 --force-run $NOTIFY_ARG $MARKET_REVIEW_ARG"
else
  echo "[pipeline] 2/9 daily_stock_analysis skipped by RUN_ANALYSIS=false"
fi

echo "[pipeline] 3/9 evidence hub"
$SCRIPT_DIR/run_evidence_hub.sh

echo "[pipeline] 4/9 valuation / expectation layer"
$SCRIPT_DIR/build_valuation_layer.sh

echo "[pipeline] 5/9 final layers"
$SCRIPT_DIR/build_final_layers.sh

echo "[pipeline] 6/9 fundamental-first gate"
$SCRIPT_DIR/build_fundamental_first.sh

echo "[pipeline] 7/9 paper portfolio"
$SCRIPT_DIR/run_paper_portfolio.sh

echo "[pipeline] 8/9 holding review"
$SCRIPT_DIR/build_holding_review.sh

echo "[pipeline] 9/9 strategy validation"
$SCRIPT_DIR/build_strategy_validation.sh

echo "[pipeline] done"
ls -1 "$PROJECT_DIR/reports"/final_score_*.md "$PROJECT_DIR/reports"/valuation_expectation_*.md "$PROJECT_DIR/reports"/final_trade_plan_*.md "$PROJECT_DIR/reports"/fundamental_first_*.md "$PROJECT_DIR/reports"/paper_portfolio_*.md "$PROJECT_DIR/reports"/holding_review_*.md "$PROJECT_DIR/reports"/strategy_validation_*.md "$PROJECT_DIR/reports"/system_status_*.md 2>/dev/null | tail -45
