#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"

cd "$PROJECT_DIR"

WAIT_SECONDS="${WAIT_SECONDS:-2700}"
SLEEP_SECONDS="${SLEEP_SECONDS:-60}"
FINAL_SCORE_PATH="$PROJECT_DIR/data/evidence_hub/final_score_${DATE_KEY}.csv"
START_TS="$(date +%s)"
while [[ ! -s "$FINAL_SCORE_PATH" ]]; do
  NOW_TS="$(date +%s)"
  ELAPSED=$((NOW_TS - START_TS))
  if (( ELAPSED >= WAIT_SECONDS )); then
    echo "[valuation] missing $FINAL_SCORE_PATH after ${WAIT_SECONDS}s; run evidence hub first" >&2
    exit 1
  fi
  echo "[valuation] waiting for $FINAL_SCORE_PATH (${ELAPSED}s/${WAIT_SECONDS}s)"
  sleep "$SLEEP_SECONDS"
done

sudo docker exec stock-server python /app/stock_ai_v21/valuation_layer_system/build_valuation_layer.py \
  --learning-pool-dir /app/data/fundamental_pool \
  --evidence-dir /app/data/evidence_hub \
  --output-dir /app/data/valuation_layer \
  --report-dir /app/reports \
  --date "$DATE_KEY"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/valuation_layer" \
  "$PROJECT_DIR/stock_ai_v21/valuation_layer_system" \
  "$PROJECT_DIR/reports"/valuation_expectation_*.md 2>/dev/null || true
