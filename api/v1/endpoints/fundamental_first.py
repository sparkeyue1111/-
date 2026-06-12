from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()
ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
REPORT_DIR = ROOT_DIR / "reports"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_csv(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append({key: normalize_value(value) for key, value in row.items()})
            if limit is not None and len(rows) >= limit:
                break
    return rows


def normalize_value(value: Any) -> Any:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"", "nan", "None"}:
        return ""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if any(mark in text for mark in [".", "e", "E"]):
            return float(text)
        return int(text)
    except Exception:
        return text


def latest_csv(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def summary_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"BUY_READY": 0, "WATCH": 0, "REJECT": 0, "PENDING_RESEARCH": 0}
    for row in candidates:
        decision = str(row.get("decision") or "")
        counts[decision] = counts.get(decision, 0) + 1
    opportunity = [row for row in candidates if row.get("decision") == "BUY_READY"]
    watch = [row for row in candidates if row.get("decision") == "WATCH"]
    return {
        "total": len(candidates),
        "buyReady": counts.get("BUY_READY", 0),
        "watch": counts.get("WATCH", 0),
        "reject": counts.get("REJECT", 0),
        "pendingResearch": counts.get("PENDING_RESEARCH", 0),
        "opportunityCount": len(opportunity),
        "watchCount": len(watch),
    }


@router.get("/dashboard")
def get_fundamental_first_dashboard() -> dict[str, Any]:
    candidate_dir = DATA_DIR / "fundamental_first"
    portfolio_dir = DATA_DIR / "paper_portfolio"
    candidates = read_json(candidate_dir / "current_fundamental_first_candidates.json", None)
    if not isinstance(candidates, list):
        candidates = read_csv(candidate_dir / "current_fundamental_first_candidates.csv")
    opportunities = [row for row in candidates if row.get("decision") == "BUY_READY"]
    watch = [row for row in candidates if row.get("decision") == "WATCH"]
    state = read_json(portfolio_dir / "paper_portfolio_state.json", {})
    holdings = read_csv(portfolio_dir / "current_paper_holdings.csv")
    equity_curve = read_csv(portfolio_dir / "paper_equity_curve.csv")
    latest_trade_path = latest_csv(portfolio_dir, "paper_trades_*.csv")
    trades = read_csv(latest_trade_path) if latest_trade_path else []
    date = ""
    if candidates:
        date = str(candidates[0].get("date") or "")
    if not date and equity_curve:
        date = str(equity_curve[-1].get("date") or "")
    latest_reports = {
        "fundamentalFirst": str(latest_csv(REPORT_DIR, "fundamental_first_*.md") or ""),
        "paperPortfolio": str(latest_csv(REPORT_DIR, "paper_portfolio_*.md") or ""),
    }
    return {
        "date": date,
        "summary": summary_from_candidates(candidates),
        "candidates": candidates,
        "opportunities": opportunities,
        "watch": watch,
        "paper": {
            "state": state if isinstance(state, dict) else {},
            "holdings": holdings,
            "equityCurve": equity_curve,
            "trades": trades,
            "latestTradeFile": latest_trade_path.name if latest_trade_path else "",
        },
        "reports": latest_reports,
    }
