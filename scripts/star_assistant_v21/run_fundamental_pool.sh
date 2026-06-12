#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
CONTAINER="${CONTAINER:-stock-server}"
POOL_SIZE="${POOL_SIZE:-50}"
ANALYZE_COUNT="${ANALYZE_COUNT:-10}"
FUNDAMENTAL_PROBE_COUNT="${FUNDAMENTAL_PROBE_COUNT:-120}"
SOURCE_TIMEOUT="${SOURCE_TIMEOUT:-25}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

sudo docker exec \
  -e POOL_SIZE="$POOL_SIZE" \
  -e ANALYZE_COUNT="$ANALYZE_COUNT" \
  -e FUNDAMENTAL_PROBE_COUNT="$FUNDAMENTAL_PROBE_COUNT" \
  -e SOURCE_TIMEOUT="$SOURCE_TIMEOUT" \
  "$CONTAINER" \
  sh -lc 'cd /app && PYTHONPATH=/app python /app/stock_ai_v21/fundamental_pool_system/run_fundamental_pool.py --env-file /app/data/app.env --output-dir /app/data/fundamental_pool --report-dir /app/reports --pool-size "$POOL_SIZE" --analyze-count "$ANALYZE_COUNT" --probe-count "$FUNDAMENTAL_PROBE_COUNT" --timeout "$SOURCE_TIMEOUT"'

sudo chown -R "$(id -un):$(id -gn)" \
  "$PROJECT_DIR/data/fundamental_pool" \
  "$PROJECT_DIR/stock_ai_v21/fundamental_pool_system" \
  "$PROJECT_DIR/reports"/fundamental_pool_*.md \
  "$PROJECT_DIR/reports"/midterm_holding_plan_*.md 2>/dev/null || true

python3 - <<'PY'
from pathlib import Path

import os
project = Path(os.environ.get("PROJECT_DIR", "/home/ubuntu/stock-ai/daily_stock_analysis"))
app_env = project / "data" / "app.env"
root_env = project / ".env"

def get_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""

def set_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    out = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

stock_list = get_value(app_env, "STOCK_LIST")
if not stock_list:
    raise SystemExit("STOCK_LIST not found in data/app.env")
set_value(root_env, "STOCK_LIST", stock_list)
print(f"Synced STOCK_LIST to .env: {stock_list}")
PY
