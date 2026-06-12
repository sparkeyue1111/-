#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"

cd "$PROJECT_DIR"
sudo docker exec stock-server python /app/stock_ai_v21/strategy_validation_system/build_strategy_validation.py \
  --final-layer-dir /app/data/final_layers \
  --learning-pool-dir /app/data/fundamental_pool \
  --validation-dir /app/data/strategy_validation \
  --report-dir /app/reports \
  --date "$DATE_KEY"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/strategy_validation" \
  "$PROJECT_DIR/stock_ai_v21/strategy_validation_system" \
  "$PROJECT_DIR/reports"/strategy_validation_*.md 2>/dev/null || true
