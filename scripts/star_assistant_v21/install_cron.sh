#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"

TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null | grep -v "scripts/star_assistant_v21" > "$TMP_CRON" || true

cat >> "$TMP_CRON" <<CRON
# 星星分析助手 v2.2：基本面优先策略流水线
5 20 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/run_fundamental_pool.sh >> $LOG_DIR/fundamental_pool_cron.log 2>&1
15 20 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_financial_statements.sh >> $LOG_DIR/financial_statements_cron.log 2>&1
5 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/run_evidence_hub.sh >> $LOG_DIR/evidence_hub_cron.log 2>&1
10 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_valuation_layer.sh >> $LOG_DIR/valuation_layer_cron.log 2>&1
15 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_final_layers.sh >> $LOG_DIR/final_layers_cron.log 2>&1
18 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_data_quality.sh >> $LOG_DIR/data_quality_cron.log 2>&1
20 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_fundamental_first.sh >> $LOG_DIR/fundamental_first_cron.log 2>&1
25 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/run_paper_portfolio.sh >> $LOG_DIR/paper_portfolio_cron.log 2>&1
30 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_holding_review.sh >> $LOG_DIR/holding_review_cron.log 2>&1
35 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_strategy_validation.sh >> $LOG_DIR/strategy_validation_cron.log 2>&1
40 22 * * 1-5 PROJECT_DIR=$PROJECT_DIR $PROJECT_DIR/scripts/star_assistant_v21/build_forward_validation.sh >> $LOG_DIR/forward_validation_cron.log 2>&1
30 3 * * 6 PROJECT_DIR=$PROJECT_DIR BACKTEST_YEARS=5 BACKTEST_MAX_CODES=50 $PROJECT_DIR/scripts/star_assistant_v21/build_historical_backtest.sh >> $LOG_DIR/historical_backtest_cron.log 2>&1
0 4 * * 6 PROJECT_DIR=$PROJECT_DIR BACKTEST_YEARS=5 BACKTEST_V2_MAX_CODES=0 BACKTEST_V2_WORKERS=4 BACKTEST_INITIAL_CAPITAL=100000 $PROJECT_DIR/scripts/star_assistant_v21/build_market_v2_backtest.sh >> $LOG_DIR/market_v2_backtest_cron.log 2>&1
CRON

crontab "$TMP_CRON"
rm -f "$TMP_CRON"

echo "Installed star_assistant_v22 cron jobs for $PROJECT_DIR"
crontab -l | grep "scripts/star_assistant_v21" || true
