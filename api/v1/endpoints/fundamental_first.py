from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response

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


COMPACT_CANDIDATE_FIELDS = {
    "date",
    "code",
    "name",
    "decision",
    "action",
    "research_status",
    "failed_gates",
    "fundamental_first_score",
    "company_quality_score",
    "financial_statement_score",
    "industry_logic_score",
    "evidence_quality_score",
    "value_gap_score",
    "trade_score",
    "trade_score_date",
    "trade_score_source",
    "market_ok",
    "current_price",
    "risk_stop",
    "data_quality_score",
    "data_quality_status",
}


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in COMPACT_CANDIDATE_FIELDS if key in row}


def portfolio_payload(portfolio_dir: Path, *, compact: bool) -> dict[str, Any]:
    state = read_json(portfolio_dir / "paper_portfolio_state.json", {})
    holdings = read_csv(portfolio_dir / "current_paper_holdings.csv")
    equity_curve = read_csv(portfolio_dir / "paper_equity_curve.csv")
    latest_trade_path = latest_csv(portfolio_dir, "paper_trades_*.csv")
    trades = read_csv(latest_trade_path) if latest_trade_path else []
    if compact:
        raw_state = state if isinstance(state, dict) else {}
        state = {
            key: raw_state.get(key)
            for key in (
                "cash",
                "equity",
                "initial_capital",
                "last_update",
                "portfolio_mode",
                "pending_orders",
                "order_rejections",
            )
            if key in raw_state
        }
        equity_curve = equity_curve[-30:]
        trades = trades[-8:]
    return {
        "state": state if isinstance(state, dict) else {},
        "holdings": holdings,
        "equityCurve": equity_curve,
        "trades": trades,
        "latestTradeFile": latest_trade_path.name if latest_trade_path else "",
    }


def summary_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "BUY_READY": 0,
        "TRADE_CANDIDATE": 0,
        "WATCH": 0,
        "RESEARCH_QUEUE": 0,
        "FUNDAMENTAL_POOL": 0,
        "REJECT": 0,
        "PENDING_RESEARCH": 0,
    }
    for row in candidates:
        decision = str(row.get("decision") or "")
        counts[decision] = counts.get(decision, 0) + 1
    opportunity = [
        row for row in candidates
        if row.get("decision") in {"BUY_READY", "TRADE_CANDIDATE"}
    ]
    watch = [
        row for row in candidates
        if row.get("decision") in {"WATCH", "RESEARCH_QUEUE", "FUNDAMENTAL_POOL", "PENDING_RESEARCH"}
    ]
    research_queue = [
        row for row in candidates
        if row.get("decision") in {"RESEARCH_QUEUE", "PENDING_RESEARCH"}
    ]
    return {
        "total": len(candidates),
        "buyReady": counts.get("BUY_READY", 0),
        "tradeCandidate": counts.get("TRADE_CANDIDATE", 0),
        "watch": counts.get("WATCH", 0),
        "researchQueue": counts.get("RESEARCH_QUEUE", 0) + counts.get("PENDING_RESEARCH", 0),
        "fundamentalPool": counts.get("FUNDAMENTAL_POOL", 0),
        "reject": counts.get("REJECT", 0),
        "pendingResearch": counts.get("PENDING_RESEARCH", 0),
        "opportunityCount": len(opportunity),
        "watchCount": len(watch),
        "researchQueueCount": len(research_queue),
    }


@router.get("/dashboard")
def get_fundamental_first_dashboard(response: Response, compact: bool = False) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"
    candidate_dir = DATA_DIR / "fundamental_first"
    portfolio_dir = DATA_DIR / "paper_portfolio"
    shadow_portfolio_dir = DATA_DIR / "paper_portfolio_shadow"
    candidates = read_json(candidate_dir / "current_fundamental_first_candidates.json", None)
    if not isinstance(candidates, list):
        candidates = read_csv(candidate_dir / "current_fundamental_first_candidates.csv")
    paper = portfolio_payload(portfolio_dir, compact=compact)
    shadow_paper = portfolio_payload(shadow_portfolio_dir, compact=compact)
    opportunities = [row for row in candidates if row.get("decision") in {"BUY_READY", "TRADE_CANDIDATE"}]
    watch = [row for row in candidates if row.get("decision") in {"WATCH", "RESEARCH_QUEUE", "FUNDAMENTAL_POOL", "PENDING_RESEARCH"}]
    quality_dir = DATA_DIR / "data_quality"
    forward_dir = DATA_DIR / "forward_validation"
    date = ""
    if candidates:
        date = str(candidates[0].get("date") or "")
    if not date and paper["equityCurve"]:
        date = str(paper["equityCurve"][-1].get("date") or "")
    quality_summary = read_json(quality_dir / "current_data_quality.json", {})
    quality_checks = read_csv(quality_dir / "current_data_quality_checks.csv")
    quality_stocks = [] if compact else read_csv(quality_dir / "current_data_quality_stock.csv", limit=80)
    forward_validation = read_json(forward_dir / "current_forward_validation.json", {})
    if compact and isinstance(forward_validation, dict):
        forward_validation = {
            key: value
            for key, value in forward_validation.items()
            if key not in {"errors", "predictions"}
        }
    latest_reports = {
        "fundamentalFirst": str(latest_csv(REPORT_DIR, "fundamental_first_*.md") or ""),
        "paperPortfolio": str(latest_csv(REPORT_DIR, "paper_portfolio_[0-9]*.md") or ""),
        "paperPortfolioShadow": str(latest_csv(REPORT_DIR, "paper_portfolio_shadow_*.md") or ""),
        "dataQuality": str(latest_csv(REPORT_DIR, "data_quality_*.md") or ""),
        "financialStatements": str(latest_csv(REPORT_DIR, "financial_statements_*.md") or ""),
        "forwardValidation": str(latest_csv(REPORT_DIR, "forward_validation_*.md") or ""),
    }
    response_candidates = [compact_candidate(row) for row in candidates] if compact else candidates
    response_opportunities = [compact_candidate(row) for row in opportunities] if compact else opportunities
    response_watch = [compact_candidate(row) for row in watch] if compact else watch
    return {
        "date": date,
        "summary": summary_from_candidates(candidates),
        "candidates": response_candidates,
        "opportunities": response_opportunities,
        "watch": response_watch,
        "paper": paper,
        "shadowPaper": shadow_paper,
        "quality": {
            "summary": quality_summary if isinstance(quality_summary, dict) else {},
            "checks": quality_checks,
            "stocks": quality_stocks,
        },
        "forwardValidation": forward_validation if isinstance(forward_validation, dict) else {},
        "reports": latest_reports,
    }


@router.get("/candidates/{code}")
def get_fundamental_candidate(code: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, max-age=60, stale-while-revalidate=120"
    normalized = "".join(char for char in str(code) if char.isdigit())[-6:].zfill(6)
    candidate_dir = DATA_DIR / "fundamental_first"
    candidates = read_json(candidate_dir / "current_fundamental_first_candidates.json", None)
    if not isinstance(candidates, list):
        candidates = read_csv(candidate_dir / "current_fundamental_first_candidates.csv")
    for row in candidates:
        row_code = "".join(char for char in str(row.get("code") or "") if char.isdigit())[-6:].zfill(6)
        if row_code == normalized:
            return row
    raise HTTPException(status_code=404, detail="candidate not found")
