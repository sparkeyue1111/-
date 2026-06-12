#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/forward_validation_system/build_forward_validation.py \
  --fundamental-first-dir /app/data/fundamental_first \
  --paper-portfolio-dir /app/data/paper_portfolio \
  --output-dir /app/data/forward_validation \
  --report-dir /app/reports \
  --date "$DATE_KEY"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/forward_validation" \
  "$PROJECT_DIR/stock_ai_v21/forward_validation_system" \
  "$PROJECT_DIR/reports"/forward_validation_*.md 2>/dev/null || true
