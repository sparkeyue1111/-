#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"

cd "$PROJECT_DIR"
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
