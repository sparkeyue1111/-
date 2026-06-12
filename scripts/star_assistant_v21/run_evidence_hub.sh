#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
POOL_LIMIT="${POOL_LIMIT:-5}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-730}"
SOURCE_TIMEOUT="${SOURCE_TIMEOUT:-35}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec \
  -e POOL_LIMIT="$POOL_LIMIT" \
  -e LOOKBACK_DAYS="$LOOKBACK_DAYS" \
  -e SOURCE_TIMEOUT="$SOURCE_TIMEOUT" \
  "$CONTAINER" \
  sh -lc 'cd /app && PYTHONPATH=/app python /app/stock_ai_v21/evidence_hub_system/run_evidence_hub.py --learning-pool-dir /app/data/fundamental_pool --evidence-dir /app/data/evidence_hub --report-dir /app/reports --pool-limit "$POOL_LIMIT" --lookback-days "$LOOKBACK_DAYS" --timeout "$SOURCE_TIMEOUT"'

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/evidence_hub" \
  "$PROJECT_DIR/stock_ai_v21/evidence_hub_system" \
  "$PROJECT_DIR/reports"/evidence_quality_*.md \
  "$PROJECT_DIR/reports"/final_score_*.md 2>/dev/null || true
