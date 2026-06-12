#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/data_quality_system/build_data_quality.py \
  --fundamental-pool-dir /app/data/fundamental_pool \
  --evidence-dir /app/data/evidence_hub \
  --valuation-dir /app/data/valuation_layer \
  --final-layer-dir /app/data/final_layers \
  --historical-backtest-dir /app/data/historical_backtest \
  --financial-statements-dir /app/data/financial_statements \
  --output-dir /app/data/data_quality \
  --report-dir /app/reports \
  --date "$DATE_KEY"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/data_quality" \
  "$PROJECT_DIR/stock_ai_v21/data_quality_system" \
  "$PROJECT_DIR/reports"/data_quality_*.md 2>/dev/null || true
