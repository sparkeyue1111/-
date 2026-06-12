#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build enhanced financial statement layer for Star Assistant v2.2.")
    parser.add_argument("--fundamental-pool-dir", default="/app/data/fundamental_pool")
    parser.add_argument("--output-dir", default="/app/data/financial_statements")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--max-codes", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=18)
    return parser.parse_args()


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        text = safe_text(value).replace(",", "").replace("%", "")
        if text in {"", "-", "--", "nan", "None"}:
            return default
        number = float(text)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def normalize_code(value: Any) -> str:
    match = re.search(r"(\d{6})", safe_text(value))
    return match.group(1) if match else safe_text(value).zfill(6)[-6:]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")


def timeout_call(fn_name: str, kwargs: dict[str, Any], timeout: int) -> tuple[pd.DataFrame, str]:
    def handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{fn_name} timeout after {timeout}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(timeout)
    try:
        data = getattr(ak, fn_name)(**kwargs)
        if isinstance(data, pd.DataFrame):
            return data, ""
        return pd.DataFrame(), f"{fn_name}: not dataframe"
    except TimeoutError as exc:
        return pd.DataFrame(), str(exc)
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:240]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def latest_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    frame = raw.copy()
    if "report_date" in frame.columns:
        frame["_report_date"] = pd.to_datetime(frame["report_date"], errors="coerce")
        latest = frame["_report_date"].dropna().max()
        if not pd.isna(latest):
            return frame[frame["_report_date"] == latest]
    return frame.head(150)


def metric_value(frame: pd.DataFrame, patterns: list[str], columns: list[str] | None = None) -> float:
    if frame.empty or "metric_name" not in frame.columns:
        return math.nan
    columns = columns or ["value", "single", "yoy", "single_yoy"]
    pattern = re.compile("|".join(patterns), re.IGNORECASE)
    subset = frame[frame["metric_name"].astype(str).str.contains(pattern, regex=True, na=False)]
    if subset.empty:
        return math.nan
    for column in columns:
        if column not in subset.columns:
            continue
        for value in subset[column].tolist():
            number = safe_float(value, math.nan)
            if not math.isnan(number):
                return number
    return math.nan


def normalize_growth(value: float) -> float:
    if math.isnan(value):
        return value
    if -10 < value < 10:
        return value * 100
    return value


def report_date_of(*frames: pd.DataFrame) -> str:
    dates = []
    for frame in frames:
        if frame.empty or "report_date" not in frame.columns:
            continue
        parsed = pd.to_datetime(frame["report_date"], errors="coerce").dropna()
        if not parsed.empty:
            dates.append(parsed.max())
    return max(dates).strftime("%Y-%m-%d") if dates else ""


def collect_statements(code: str, timeout: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    errors = []
    calls = {
        "income": "stock_financial_benefit_new_ths",
        "balance": "stock_financial_debt_new_ths",
        "cash": "stock_financial_cash_new_ths",
    }
    result: dict[str, pd.DataFrame] = {}
    for key, fn_name in calls.items():
        frame, error = timeout_call(fn_name, {"symbol": code, "indicator": "按报告期"}, timeout)
        if error:
            errors.append(f"{key}:{error}")
        result[key] = latest_frame(frame)
        time.sleep(0.08)
    return result, errors


def score_metrics(metrics: dict[str, float], coverage: float, errors: list[str]) -> tuple[float, list[str], list[str]]:
    score = 50.0
    notes: list[str] = []
    warnings: list[str] = []

    revenue_yoy = metrics["revenue_yoy"]
    if not math.isnan(revenue_yoy):
        if revenue_yoy >= 20:
            score += 12
            notes.append("营收同比高增长")
        elif revenue_yoy >= 8:
            score += 7
            notes.append("营收同比稳健")
        elif revenue_yoy < -15:
            score -= 16
            warnings.append("营收同比明显下滑")
        elif revenue_yoy < 0:
            score -= 8
            warnings.append("营收同比转负")

    net_profit_yoy = metrics["net_profit_yoy"]
    if not math.isnan(net_profit_yoy):
        if net_profit_yoy >= 30:
            score += 16
            notes.append("净利润同比弹性强")
        elif net_profit_yoy >= 10:
            score += 9
            notes.append("净利润同比增长")
        elif net_profit_yoy < -25:
            score -= 22
            warnings.append("净利润同比大幅下滑")
        elif net_profit_yoy < 0:
            score -= 10
            warnings.append("净利润同比转负")

    ocf_to_profit = metrics["ocf_to_profit"]
    if not math.isnan(ocf_to_profit):
        if ocf_to_profit >= 0.8:
            score += 15
            notes.append("经营现金流对利润覆盖较好")
        elif ocf_to_profit >= 0.3:
            score += 6
            notes.append("经营现金流仍能覆盖部分利润")
        elif ocf_to_profit < 0:
            score -= 20
            warnings.append("经营现金流为负或利润质量差")
        else:
            score -= 10
            warnings.append("经营现金流/利润偏低")

    debt_ratio = metrics["debt_ratio"]
    if not math.isnan(debt_ratio):
        if debt_ratio <= 45:
            score += 8
            notes.append("资产负债率较低")
        elif debt_ratio <= 60:
            score += 4
        elif debt_ratio >= 75:
            score -= 16
            warnings.append("资产负债率偏高")

    receivable_to_revenue = metrics["receivable_to_revenue"]
    if not math.isnan(receivable_to_revenue):
        if receivable_to_revenue <= 30:
            score += 5
        elif receivable_to_revenue >= 80:
            score -= 14
            warnings.append("应收账款占收入偏高")
        elif receivable_to_revenue >= 55:
            score -= 7
            warnings.append("应收账款压力需复核")

    inventory_to_revenue = metrics["inventory_to_revenue"]
    if not math.isnan(inventory_to_revenue):
        if inventory_to_revenue <= 45:
            score += 4
        elif inventory_to_revenue >= 110:
            score -= 12
            warnings.append("存货占收入偏高")
        elif inventory_to_revenue >= 75:
            score -= 6
            warnings.append("存货压力需复核")

    goodwill_to_assets = metrics["goodwill_to_assets"]
    if not math.isnan(goodwill_to_assets):
        if goodwill_to_assets >= 15:
            score -= 10
            warnings.append("商誉占资产比例偏高")
        elif goodwill_to_assets <= 3:
            score += 3

    if coverage < 45:
        score -= 15
        warnings.append("三表关键字段覆盖不足")
    elif coverage >= 75:
        score += 5
        notes.append("三表字段覆盖较完整")
    if errors:
        score -= min(18, len(errors) * 6)
        warnings.append("部分三表接口异常")
    return clamp(score), notes, warnings


def analyze_code(code: str, name: str, timeout: int) -> dict[str, Any]:
    statements, errors = collect_statements(code, timeout)
    income = statements.get("income", pd.DataFrame())
    balance = statements.get("balance", pd.DataFrame())
    cash = statements.get("cash", pd.DataFrame())

    revenue = metric_value(income, [r"total_operat.*income", r"operat.*income", r"revenue", r"营业.*收入"], ["value", "single"])
    revenue_yoy = normalize_growth(metric_value(income, [r"total_operat.*income", r"operat.*income", r"revenue", r"营业.*收入"], ["yoy", "single_yoy"]))
    net_profit = metric_value(income, [r"parent.*net.*profit", r"net_profit", r"归母.*净利润", r"净利润"], ["value", "single"])
    net_profit_yoy = normalize_growth(metric_value(income, [r"parent.*net.*profit", r"net_profit", r"归母.*净利润", r"净利润"], ["yoy", "single_yoy"]))
    total_assets = metric_value(balance, [r"debt_and_equity_total", r"asset.*total", r"total_assets", r"资产总计", r"总资产"], ["value", "single"])
    total_liabilities = metric_value(balance, [r"^debt_total$", r"liab.*total", r"total_liab", r"负债合计", r"总负债"], ["value", "single"])
    receivable = metric_value(balance, [r"accounts_receivable", r"应收账款"], ["value", "single"])
    inventory = metric_value(balance, [r"inventory", r"存货"], ["value", "single"])
    goodwill = metric_value(balance, [r"goodwill", r"商誉"], ["value", "single"])
    ocf = metric_value(cash, [r"net_operate_cash_flow", r"net_operat.*cash", r"operate.*cash.*net", r"net_cash.*operat", r"经营.*现金.*净", r"经营活动.*现金流量净额"], ["value", "single"])

    metrics = {
        "revenue": revenue,
        "revenue_yoy": revenue_yoy,
        "net_profit": net_profit,
        "net_profit_yoy": net_profit_yoy,
        "operating_cash_flow": ocf,
        "ocf_to_profit": ocf / net_profit if not math.isnan(ocf) and not math.isnan(net_profit) and abs(net_profit) > 1 else math.nan,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "debt_ratio": total_liabilities / total_assets * 100 if not math.isnan(total_assets) and total_assets > 0 and not math.isnan(total_liabilities) else math.nan,
        "receivable": receivable,
        "receivable_to_revenue": receivable / revenue * 100 if not math.isnan(receivable) and not math.isnan(revenue) and revenue > 0 else math.nan,
        "inventory": inventory,
        "inventory_to_revenue": inventory / revenue * 100 if not math.isnan(inventory) and not math.isnan(revenue) and revenue > 0 else math.nan,
        "goodwill": goodwill,
        "goodwill_to_assets": goodwill / total_assets * 100 if not math.isnan(goodwill) and not math.isnan(total_assets) and total_assets > 0 else math.nan,
    }
    available = sum(0 if math.isnan(value) else 1 for value in metrics.values())
    coverage = available / len(metrics) * 100
    score, notes, warnings = score_metrics(metrics, coverage, errors)
    return {
        "date": datetime.now().strftime("%Y%m%d"),
        "code": code,
        "name": name,
        "report_date": report_date_of(income, balance, cash),
        "financial_statement_score": round(score, 2),
        "statement_coverage_score": round(coverage, 2),
        "available_statement_metric_count": available,
        "financial_statement_notes": "；".join(notes) if notes else "三表未形成明显正向证据",
        "financial_statement_warnings": "；".join(warnings) if warnings else "三表暂无硬风险",
        "statement_errors": "；".join(errors),
        **{f"stmt_{key}": value for key, value in metrics.items()},
    }


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    pool_path = Path(args.fundamental_pool_dir) / "current_learning_pool.csv"
    pool = read_csv(pool_path)
    if pool.empty:
        raise SystemExit("missing current_learning_pool.csv; run fundamental pool first")
    if "score" in pool.columns:
        pool = pool.sort_values("score", ascending=False)
    rows: list[dict[str, Any]] = []
    for idx, (_, row) in enumerate(pool.head(args.max_codes).iterrows(), start=1):
        code = normalize_code(row.get("code"))
        name = safe_text(row.get("name"))
        print(f"[financial-statements] {idx}/{min(len(pool), args.max_codes)} {code} {name}", flush=True)
        rows.append(analyze_code(code, name, args.timeout))
    return rows


def write_outputs(args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_key = args.date
    for row in rows:
        row["date"] = date_key
    df = pd.DataFrame(rows)
    for path in [output_dir / f"financial_statement_scores_{date_key}.csv", output_dir / "current_financial_statement_scores.csv"]:
        df.to_csv(path, index=False, encoding="utf-8-sig")
    for path in [output_dir / f"financial_statement_scores_{date_key}.json", output_dir / "current_financial_statement_scores.json"]:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# 财务三表增强层 V1 - {date_key}",
        "",
        "定位：用利润表、资产负债表、现金流量表补充财务摘要，重点看利润质量、应收/存货压力、负债和商誉风险。",
        "",
        "| 排名 | 代码 | 名称 | 三表分 | 覆盖度 | 报告期 | 提醒 |",
        "|---:|---|---|---:|---:|---|---|",
    ]
    ordered = sorted(rows, key=lambda item: safe_float(item.get("financial_statement_score"), 0), reverse=True)
    for idx, row in enumerate(ordered, start=1):
        warn = safe_text(row.get("financial_statement_warnings")) or safe_text(row.get("financial_statement_notes"))
        lines.append(f"| {idx} | {row['code']} | {row['name']} | {safe_float(row.get('financial_statement_score'), 0):.2f} | {safe_float(row.get('statement_coverage_score'), 0):.2f} | {row.get('report_date', '')} | {warn} |")
    report_path = report_dir / f"financial_statements_{date_key}.md"
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows = build(args)
    write_outputs(args, rows)
    avg_score = sum(safe_float(row.get("financial_statement_score"), 0) for row in rows) / max(len(rows), 1)
    print(json.dumps({"ok": True, "rows": len(rows), "avg_score": round(avg_score, 2)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
