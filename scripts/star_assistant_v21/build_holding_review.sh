#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"

cd "$PROJECT_DIR"
sudo docker exec stock-server python /app/stock_ai_v21/holding_review_system/build_holding_review.py \
  --final-layer-dir /app/data/final_layers \
  --learning-pool-dir /app/data/fundamental_pool \
  --output-dir /app/data/holding_review \
  --report-dir /app/reports \
  --date "$DATE_KEY"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/holding_review" \
  "$PROJECT_DIR/stock_ai_v21/holding_review_system" \
  "$PROJECT_DIR/reports"/holding_review_*.md 2>/dev/null || true
