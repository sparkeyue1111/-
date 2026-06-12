#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build forward validation ledger for Star Assistant v2.2.")
    parser.add_argument("--fundamental-first-dir", default="/app/data/fundamental_first")
    parser.add_argument("--paper-portfolio-dir", default="/app/data/paper_portfolio")
    parser.add_argument("--output-dir", default="/app/data/forward_validation")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--horizons", default="30,60,90")
    parser.add_argument("--sleep-sec", type=float, default=0.08)
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


def normalize_code(value: Any) -> str:
    match = re.search(r"(\d{6})", safe_text(value))
    return match.group(1) if match else safe_text(value).zfill(6)[-6:]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str, "signal_date": str}, encoding="utf-8-sig")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def normalize_hist(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    date_col = "日期" if "日期" in frame.columns else ("date" if "date" in frame.columns else None)
    close_col = "收盘" if "收盘" in frame.columns else ("close" if "close" in frame.columns else None)
    if not date_col or not close_col:
        return pd.DataFrame()
    out = pd.DataFrame({
        "date": pd.to_datetime(frame[date_col], errors="coerce"),
        "close": pd.to_numeric(frame[close_col], errors="coerce"),
    }).dropna().sort_values("date")
    return out


def fetch_history(code: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, str]:
    try:
        frame = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        return normalize_hist(frame), ""
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:160]}"


def close_at_or_after(hist: pd.DataFrame, target: pd.Timestamp) -> tuple[float, str]:
    if hist.empty:
        return math.nan, ""
    subset = hist[hist["date"] >= target]
    if subset.empty:
        return math.nan, ""
    row = subset.iloc[0]
    return float(row["close"]), row["date"].strftime("%Y-%m-%d")


def snapshot_predictions(args: argparse.Namespace, existing: pd.DataFrame) -> pd.DataFrame:
    candidates = read_csv(Path(args.fundamental_first_dir) / "current_fundamental_first_candidates.csv")
    if candidates.empty:
        return existing
    today_rows = []
    state = read_json(Path(args.paper_portfolio_dir) / "paper_portfolio_state.json", {})
    held_codes = {normalize_code(item.get("code")) for item in state.get("positions", [])} if isinstance(state, dict) else set()
    for _, row in candidates.iterrows():
        code = normalize_code(row.get("code"))
        today_rows.append({
            "signal_date": args.date,
            "code": code,
            "name": safe_text(row.get("name")),
            "decision": safe_text(row.get("decision")),
            "current_price": safe_float(row.get("current_price")),
            "fundamental_first_score": safe_float(row.get("fundamental_first_score")),
            "company_quality_score": safe_float(row.get("company_quality_score")),
            "financial_quality_score": safe_float(row.get("financial_quality_score")),
            "financial_statement_score": safe_float(row.get("financial_statement_score")),
            "data_quality_score": safe_float(row.get("data_quality_score")),
            "evidence_quality_score": safe_float(row.get("evidence_quality_score")),
            "valuation_score": safe_float(row.get("valuation_score")),
            "expectation_gap_score": safe_float(row.get("expectation_gap_score")),
            "trade_score": safe_float(row.get("trade_score")),
            "failed_gates": safe_text(row.get("failed_gates")),
            "in_paper_position": code in held_codes,
        })
    today = pd.DataFrame(today_rows)
    if existing.empty:
        merged = today
    else:
        existing = existing.copy()
        existing["code"] = existing["code"].map(normalize_code)
        existing["signal_date"] = existing["signal_date"].astype(str)
        keep = existing[~((existing["signal_date"] == args.date) & existing["code"].isin(today["code"]))]
        merged = pd.concat([keep, today], ignore_index=True)
    return merged.sort_values(["signal_date", "fundamental_first_score"], ascending=[True, False]).reset_index(drop=True)


def update_outcomes(predictions: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, list[str]]:
    if predictions.empty:
        return predictions, []
    horizons = [int(item.strip()) for item in args.horizons.split(",") if item.strip()]
    today = pd.to_datetime(args.date, format="%Y%m%d", errors="coerce")
    if pd.isna(today):
        today = pd.Timestamp.today().normalize()
    errors: list[str] = []
    predictions = predictions.copy()
    predictions["code"] = predictions["code"].map(normalize_code)
    predictions["signal_date"] = predictions["signal_date"].astype(str)
    for horizon in horizons:
        for column in [f"return_{horizon}d", f"close_{horizon}d", f"close_date_{horizon}d", f"validated_{horizon}d"]:
            if column not in predictions.columns:
                predictions[column] = "" if "date" in column else math.nan
    due = predictions[predictions["signal_date"].map(lambda value: (today - pd.to_datetime(value, format="%Y%m%d", errors="coerce")).days if not pd.isna(pd.to_datetime(value, format="%Y%m%d", errors="coerce")) else -1).ge(min(horizons))]
    for code in sorted(due["code"].dropna().unique()):
        code_rows = predictions[predictions["code"] == code]
        min_signal = code_rows["signal_date"].min()
        hist, error = fetch_history(code, min_signal, args.date)
        if error:
            errors.append(f"{code}:{error}")
            continue
        for idx in code_rows.index:
            signal_date = pd.to_datetime(predictions.at[idx, "signal_date"], format="%Y%m%d", errors="coerce")
            if pd.isna(signal_date):
                continue
            entry_price = safe_float(predictions.at[idx, "current_price"], math.nan)
            if math.isnan(entry_price) or entry_price <= 0:
                entry_price, _ = close_at_or_after(hist, signal_date)
            for horizon in horizons:
                if (today - signal_date).days < horizon:
                    continue
                close, close_date = close_at_or_after(hist, signal_date + timedelta(days=horizon))
                if math.isnan(close) or math.isnan(entry_price) or entry_price <= 0:
                    continue
                predictions.at[idx, f"close_{horizon}d"] = round(close, 4)
                predictions.at[idx, f"close_date_{horizon}d"] = close_date
                predictions.at[idx, f"return_{horizon}d"] = round((close / entry_price - 1) * 100, 4)
                predictions.at[idx, f"validated_{horizon}d"] = True
        time.sleep(args.sleep_sec)
    return predictions, errors


def summarize(predictions: pd.DataFrame, args: argparse.Namespace, errors: list[str]) -> dict[str, Any]:
    horizons = [int(item.strip()) for item in args.horizons.split(",") if item.strip()]
    summary: dict[str, Any] = {
        "date": args.date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prediction_count": int(len(predictions)),
        "today_prediction_count": int((predictions.get("signal_date", pd.Series(dtype=str)).astype(str) == args.date).sum()) if not predictions.empty else 0,
        "errors": errors[:20],
        "groups": {},
    }
    if predictions.empty:
        return summary
    for decision, group in predictions.groupby("decision"):
        item: dict[str, Any] = {"count": int(len(group))}
        for horizon in horizons:
            ret_col = f"return_{horizon}d"
            validated = pd.to_numeric(group.get(ret_col), errors="coerce").dropna()
            item[f"validated_{horizon}d"] = int(len(validated))
            item[f"avg_return_{horizon}d"] = round(float(validated.mean()), 4) if len(validated) else None
            item[f"hit_rate_{horizon}d"] = round(float((validated > 0).mean() * 100), 2) if len(validated) else None
        summary["groups"][safe_text(decision) or "UNKNOWN"] = item
    return summary


def write_report(path: Path, summary: dict[str, Any], predictions: pd.DataFrame, args: argparse.Namespace) -> None:
    lines = [
        f"# 前向验证系统 V1 - {args.date}",
        "",
        "定位：保存每天当时的策略判断，未来自动回填 30/60/90 天收益，用真实前向结果验证 AI/规则是否有效。",
        "",
        f"- 累计预测数：{summary['prediction_count']}",
        f"- 今日预测数：{summary['today_prediction_count']}",
        "",
        "## 分组表现",
        "| 决策 | 样本数 | 30日样本 | 30日均值 | 30日胜率 | 60日样本 | 60日均值 | 60日胜率 | 90日样本 | 90日均值 | 90日胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for decision, item in summary.get("groups", {}).items():
        lines.append(
            f"| {decision} | {item.get('count', 0)} | "
            f"{item.get('validated_30d', 0)} | {item.get('avg_return_30d')} | {item.get('hit_rate_30d')} | "
            f"{item.get('validated_60d', 0)} | {item.get('avg_return_60d')} | {item.get('hit_rate_60d')} | "
            f"{item.get('validated_90d', 0)} | {item.get('avg_return_90d')} | {item.get('hit_rate_90d')} |"
        )
    lines += ["", "## 今日记录"]
    today = predictions[predictions["signal_date"].astype(str) == args.date].copy() if not predictions.empty else pd.DataFrame()
    if today.empty:
        lines.append("- 今日无策略池记录。")
    else:
        lines += ["| 代码 | 名称 | 决策 | 总分 | 交易分 | 数据质量 | 三表分 |", "|---|---|---|---:|---:|---:|---:|"]
        for _, row in today.head(20).iterrows():
            lines.append(f"| {row['code']} | {row['name']} | {row['decision']} | {safe_float(row.get('fundamental_first_score'), 0):.2f} | {safe_float(row.get('trade_score'), 0):.2f} | {safe_float(row.get('data_quality_score'), 0):.2f} | {safe_float(row.get('financial_statement_score'), 0):.2f} |")
    if summary.get("errors"):
        lines += ["", "## 数据更新错误"]
        lines.extend(f"- {error}" for error in summary["errors"])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "forward_predictions.csv"
    predictions = read_csv(ledger_path)
    predictions = snapshot_predictions(args, predictions)
    predictions, errors = update_outcomes(predictions, args)
    summary = summarize(predictions, args, errors)
    predictions.to_csv(ledger_path, index=False, encoding="utf-8-sig")
    predictions.to_csv(output_dir / "current_forward_predictions.csv", index=False, encoding="utf-8-sig")
    for path in [output_dir / f"forward_validation_{args.date}.json", output_dir / "current_forward_validation.json"]:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_dir / f"forward_validation_{args.date}.md", summary, predictions, args)
    print(json.dumps({"ok": True, "prediction_count": summary["prediction_count"], "today": summary["today_prediction_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
