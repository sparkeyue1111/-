#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
YEARS="${BACKTEST_YEARS:-5}"
MAX_CODES="${BACKTEST_MAX_CODES:-50}"
INITIAL_CAPITAL="${BACKTEST_INITIAL_CAPITAL:-100000}"
THRESHOLD="${BACKTEST_PORTFOLIO_THRESHOLD:-80}"

cd "$PROJECT_DIR"
sudo docker exec stock-server python /app/stock_ai_v21/historical_backtest_system/build_portfolio_backtest.py \
  --learning-pool-dir /app/data/fundamental_pool \
  --output-dir /app/data/historical_backtest \
  --report-dir /app/reports \
  --date "$DATE_KEY" \
  --years "$YEARS" \
  --max-codes "$MAX_CODES" \
  --initial-capital "$INITIAL_CAPITAL" \
  --threshold "$THRESHOLD"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/historical_backtest" \
  "$PROJECT_DIR/stock_ai_v21/historical_backtest_system" \
  "$PROJECT_DIR/reports"/portfolio_backtest_*.md 2>/dev/null || true
