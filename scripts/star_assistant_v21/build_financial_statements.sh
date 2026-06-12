#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
DATE_KEY="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
MAX_CODES="${FINANCIAL_STATEMENT_MAX_CODES:-50}"
SOURCE_TIMEOUT="${SOURCE_TIMEOUT:-18}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/financial_statements_system/build_financial_statements.py \
  --fundamental-pool-dir /app/data/fundamental_pool \
  --output-dir /app/data/financial_statements \
  --report-dir /app/reports \
  --date "$DATE_KEY" \
  --max-codes "$MAX_CODES" \
  --timeout "$SOURCE_TIMEOUT"

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/financial_statements" \
  "$PROJECT_DIR/stock_ai_v21/financial_statements_system" \
  "$PROJECT_DIR/reports"/financial_statements_*.md 2>/dev/null || true
