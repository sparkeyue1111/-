#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
MODE="${1:-${LIGHT_MONITOR_MODE:-intraday}}"
DATE_KEY="${2:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" python /app/stock_ai_v21/light_monitor_system/run_light_monitor.py \
  --mode "$MODE" \
  --date "$DATE_KEY" \
  --candidate-dir /app/data/fundamental_first \
  --portfolio-dir /app/data/paper_portfolio \
  --output-dir /app/data/light_monitor \
  --report-dir /app/reports

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/light_monitor" \
  "$PROJECT_DIR/reports"/light_monitor_*.md 2>/dev/null || true
