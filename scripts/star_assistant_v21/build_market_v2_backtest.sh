#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
YEARS="${BACKTEST_YEARS:-5}"
MAX_CODES="${BACKTEST_V2_MAX_CODES:-0}"
INITIAL_CAPITAL="${BACKTEST_INITIAL_CAPITAL:-100000}"
SCORE_VERSION="${BACKTEST_SCORE_VERSION:-v21}"
THRESHOLD="${BACKTEST_PORTFOLIO_THRESHOLD:-76}"
HOLD_THRESHOLD="${BACKTEST_HOLD_THRESHOLD:-68}"
MAX_NEW_PER_REBALANCE="${BACKTEST_MAX_NEW_PER_REBALANCE:-2}"
MARKET_GUARD="${BACKTEST_MARKET_GUARD:-1}"
STICKY_HOLD="${BACKTEST_STICKY_HOLD:-1}"
TIMEOUT="${SOURCE_TIMEOUT:-12}"
FORCE_UNIVERSE_REFRESH="${BACKTEST_V2_FORCE_UNIVERSE_REFRESH:-0}"
WORKERS="${BACKTEST_V2_WORKERS:-4}"

cd "$PROJECT_DIR"

cmd=(
  sudo docker exec stock-server
  python /app/stock_ai_v21/historical_backtest_system/build_market_portfolio_backtest_v2.py
  --output-dir /app/data/historical_backtest
  --report-dir /app/reports
  --date "$DATE_KEY"
  --years "$YEARS"
  --max-codes "$MAX_CODES"
  --initial-capital "$INITIAL_CAPITAL"
  --score-version "$SCORE_VERSION"
  --threshold "$THRESHOLD"
  --hold-threshold "$HOLD_THRESHOLD"
  --max-new-per-rebalance "$MAX_NEW_PER_REBALANCE"
  --timeout "$TIMEOUT"
  --workers "$WORKERS"
)

if [ "$MARKET_GUARD" = "0" ]; then
  cmd+=(--no-market-guard)
else
  cmd+=(--market-guard)
fi

if [ "$STICKY_HOLD" = "0" ]; then
  cmd+=(--no-sticky-hold)
else
  cmd+=(--sticky-hold)
fi

if [ "$FORCE_UNIVERSE_REFRESH" = "1" ]; then
  cmd+=(--force-universe-refresh)
fi

"${cmd[@]}"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/historical_backtest" \
  "$PROJECT_DIR/stock_ai_v21/historical_backtest_system" \
  "$PROJECT_DIR/reports"/market_v2_backtest_*.md 2>/dev/null || true
