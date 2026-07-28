#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
INITIAL_CAPITAL="${PAPER_INITIAL_CAPITAL:-1000000}"
MAX_POSITIONS="${PAPER_MAX_POSITIONS:-5}"
MAX_POSITION_PCT="${PAPER_MAX_POSITION_PCT:-0.20}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/paper_portfolio_system/run_paper_portfolio.py \
  --candidate-dir /app/data/fundamental_first \
  --portfolio-dir /app/data/paper_portfolio \
  --report-dir /app/reports \
  --date "$DATE_KEY" \
  --initial-capital "$INITIAL_CAPITAL" \
  --max-positions "$MAX_POSITIONS" \
  --max-position-pct "$MAX_POSITION_PCT" \
  --mode strict

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/paper_portfolio_system/run_paper_portfolio.py \
  --candidate-dir /app/data/fundamental_first \
  --portfolio-dir /app/data/paper_portfolio_shadow \
  --report-dir /app/reports \
  --date "$DATE_KEY" \
  --initial-capital "${SHADOW_INITIAL_CAPITAL:-$INITIAL_CAPITAL}" \
  --max-positions "${SHADOW_MAX_POSITIONS:-5}" \
  --max-position-pct "${SHADOW_MAX_POSITION_PCT:-0.10}" \
  --entry-threshold "${SHADOW_ENTRY_THRESHOLD:-70}" \
  --hold-threshold "${SHADOW_HOLD_THRESHOLD:-60}" \
  --min-total-score "${SHADOW_MIN_TOTAL_SCORE:-65}" \
  --lot-size "${SHADOW_LOT_SIZE:-1}" \
  --mode shadow

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/paper_portfolio" \
  "$PROJECT_DIR/data/paper_portfolio_shadow" \
  "$PROJECT_DIR/stock_ai_v21/paper_portfolio_system" \
  "$PROJECT_DIR/reports"/paper_portfolio_*.md \
  "$PROJECT_DIR/reports"/paper_portfolio_shadow_*.md 2>/dev/null || true
