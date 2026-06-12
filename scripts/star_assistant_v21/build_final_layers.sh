#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" sh -lc 'cd /app && PYTHONPATH=/app python /app/stock_ai_v21/final_layers_system/build_final_layers.py --learning-pool-dir /app/data/fundamental_pool --evidence-dir /app/data/evidence_hub --valuation-dir /app/data/valuation_layer --output-dir /app/data/final_layers --report-dir /app/reports'

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/final_layers" \
  "$PROJECT_DIR/stock_ai_v21/final_layers_system" \
  "$PROJECT_DIR/data/valuation_layer" \
  "$PROJECT_DIR/reports"/final_trade_plan_*.md \
  "$PROJECT_DIR/reports"/final_review_*.md \
  "$PROJECT_DIR/reports"/system_status_*.md 2>/dev/null || true
