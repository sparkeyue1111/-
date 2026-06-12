#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec "$CONTAINER" sh -lc 'cd /app && PYTHONPATH=/app python /app/stock_ai_v21/learning_pool_system/build_decision_overlay.py --data-dir /app/data/fundamental_pool --report-dir /app/reports'
sudo chown "$(id -un):$(id -gn)" "$PROJECT_DIR"/reports/decision_overlay_*.md 2>/dev/null || true
