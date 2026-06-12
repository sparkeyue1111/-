#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build data source quality layer for Star Assistant v2.2.")
    parser.add_argument("--fundamental-pool-dir", default="/app/data/fundamental_pool")
    parser.add_argument("--evidence-dir", default="/app/data/evidence_hub")
    parser.add_argument("--valuation-dir", default="/app/data/valuation_layer")
    parser.add_argument("--final-layer-dir", default="/app/data/final_layers")
    parser.add_argument("--historical-backtest-dir", default="/app/data/historical_backtest")
    parser.add_argument("--financial-statements-dir", default="/app/data/financial_statements")
    parser.add_argument("--output-dir", default="/app/data/data_quality")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--min-spot-rows", type=int, default=3500)
    parser.add_argument("--max-missing-rate", type=float, default=0.35)
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


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")


def latest_file(directory: Path, pattern: str, date_key: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    eligible = []
    for path in files:
        match = re.search(r"(\d{8})", path.name)
        if match and match.group(1) <= date_key:
            eligible.append(path)
    return eligible[-1] if eligible else (files[-1] if files else None)


def dated_or_latest_file(directory: Path, exact_name: str, pattern: str, date_key: str) -> tuple[Path | None, bool]:
    exact = directory / exact_name
    if exact.exists():
        return exact, False
    fallback = latest_file(directory, pattern, date_key)
    return fallback, bool(fallback)


def rate_missing(frame: pd.DataFrame, columns: list[str]) -> float:
    if frame.empty or not columns:
        return 1.0
    present = [column for column in columns if column in frame.columns]
    if not present:
        return 1.0
    missing = frame[present].isna() | (frame[present].astype(str).isin(["", "nan", "None", "--", "-"]))
    return float(missing.sum().sum()) / float(len(frame) * len(present))


def status_from_score(score: float) -> str:
    if score >= 80:
        return "OK"
    if score >= 60:
        return "WARN"
    return "BAD"


def probe_spot_source(args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    raw = pd.DataFrame()
    source_name = ""
    for fn_name in ("stock_zh_a_spot", "stock_zh_a_spot_em"):
        try:
            candidate = getattr(ak, fn_name)()
            if isinstance(candidate, pd.DataFrame) and not candidate.empty:
                raw = candidate
                source_name = fn_name
                break
            errors.append(f"{fn_name}: empty")
        except Exception as exc:
            errors.append(f"{fn_name}: {type(exc).__name__}: {str(exc)[:180]}")
    if raw.empty:
        return {
            "check": "akshare_spot",
            "status": "BAD",
            "score": 0.0,
            "source": "",
            "row_count": 0,
            "missing_rate": 1.0,
            "warnings": "；".join(errors) or "行情接口无数据",
        }
    code_col = pick_column(raw, ["代码", "code", "股票代码"])
    name_col = pick_column(raw, ["名称", "name", "股票名称"])
    price_col = pick_column(raw, ["最新价", "最新", "现价", "trade", "close"])
    pct_col = pick_column(raw, ["涨跌幅", "涨幅", "changepercent", "pct_chg"])
    amount_col = pick_column(raw, ["成交额", "amount"])
    required = [code_col, name_col, price_col, pct_col, amount_col]
    missing_columns = ["代码", "名称", "价格", "涨跌幅", "成交额"][:]
    missing_columns = [label for label, column in zip(missing_columns, required) if column is None]
    present = [column for column in required if column is not None]
    missing_rate = rate_missing(raw, present)
    score = 100.0
    if len(raw) < args.min_spot_rows:
        score -= 25
    score -= len(missing_columns) * 18
    score -= min(45, missing_rate * 100)
    if price_col:
        price = pd.to_numeric(raw[price_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
        abnormal = float(((price <= 0) | (price > 10000)).sum()) / max(len(raw), 1)
        score -= min(20, abnormal * 100)
    warnings = []
    if errors:
        warnings.append("备用行情源报错：" + "；".join(errors))
    if missing_columns:
        warnings.append("缺少字段：" + "、".join(missing_columns))
    if missing_rate > args.max_missing_rate:
        warnings.append(f"核心字段缺失率过高：{missing_rate:.1%}")
    if len(raw) < args.min_spot_rows:
        warnings.append(f"行情股票数偏少：{len(raw)}")
    score = clamp(score)
    return {
        "check": "akshare_spot",
        "status": status_from_score(score),
        "score": round(score, 2),
        "source": source_name,
        "row_count": len(raw),
        "missing_rate": round(missing_rate, 4),
        "warnings": "；".join(warnings) if warnings else "行情源字段和数量正常",
    }


def check_csv_file(label: str, path: Path | None, required_columns: list[str], args: argparse.Namespace, stale: bool = False) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "check": label,
            "status": "BAD",
            "score": 0.0,
            "path": str(path or ""),
            "row_count": 0,
            "missing_rate": 1.0,
            "warnings": "文件缺失",
        }
    try:
        frame = read_csv(path)
    except Exception as exc:
        return {
            "check": label,
            "status": "BAD",
            "score": 0.0,
            "path": str(path),
            "row_count": 0,
            "missing_rate": 1.0,
            "warnings": f"读取失败：{type(exc).__name__}: {str(exc)[:180]}",
        }
    missing_columns = [column for column in required_columns if column not in frame.columns]
    missing_rate = rate_missing(frame, [column for column in required_columns if column in frame.columns])
    score = 100.0
    if frame.empty:
        score -= 60
    score -= len(missing_columns) * 18
    score -= min(45, missing_rate * 100)
    warnings = []
    if frame.empty:
        warnings.append("文件为空")
    if missing_columns:
        warnings.append("缺少字段：" + "、".join(missing_columns))
    if missing_rate > args.max_missing_rate:
        warnings.append(f"核心字段缺失率过高：{missing_rate:.1%}")
    if stale:
        score -= 12
        warnings.append(f"未找到当日文件，使用最新文件：{path.name}")
    score = clamp(score)
    return {
        "check": label,
        "status": status_from_score(score),
        "score": round(score, 2),
        "path": str(path),
        "row_count": len(frame),
        "missing_rate": round(missing_rate, 4),
        "warnings": "；".join(warnings) if warnings else "文件存在且核心字段可用",
    }


def build_stock_quality(pool: pd.DataFrame, financial_statements: pd.DataFrame) -> pd.DataFrame:
    if pool.empty:
        return pd.DataFrame(columns=["code", "name", "data_quality_score", "data_quality_status", "data_quality_warnings"])
    rows = []
    fs_by_code: dict[str, dict[str, Any]] = {}
    if not financial_statements.empty and "code" in financial_statements.columns:
        financial_statements["code"] = financial_statements["code"].map(normalize_code)
        fs_by_code = {normalize_code(row.get("code")): row.to_dict() for _, row in financial_statements.iterrows()}
    for _, row in pool.iterrows():
        code = normalize_code(row.get("code"))
        score = 100.0
        warnings = []
        required = ["code", "name", "price", "amount", "score", "fundamental_score", "available_metric_count"]
        for column in required:
            value = row.get(column)
            if safe_text(value) in {"", "nan", "None"}:
                score -= 8
                warnings.append(f"{column}缺失")
        if safe_float(row.get("price"), 0) <= 0:
            score -= 12
            warnings.append("价格异常")
        if safe_float(row.get("amount"), 0) <= 0:
            score -= 12
            warnings.append("成交额异常")
        if safe_float(row.get("available_metric_count"), 0) < 3:
            score -= 18
            warnings.append("财务摘要指标不足")
        fs = fs_by_code.get(code)
        if fs:
            fs_score = safe_float(fs.get("financial_statement_score"), math.nan)
            coverage = safe_float(fs.get("statement_coverage_score"), math.nan)
            if math.isnan(fs_score) or fs_score <= 0:
                score -= 15
                warnings.append("三表增强分缺失")
            elif fs_score < 45:
                score -= 10
                warnings.append("三表增强层质量偏弱")
            if not math.isnan(coverage) and coverage < 40:
                score -= 10
                warnings.append("三表覆盖度不足")
        else:
            score -= 12
            warnings.append("尚未生成三表增强数据")
        score = clamp(score)
        rows.append({
            "code": code,
            "name": safe_text(row.get("name")),
            "data_quality_score": round(score, 2),
            "data_quality_status": status_from_score(score),
            "data_quality_warnings": "；".join(warnings) if warnings else "股票级关键字段完整",
        })
    return pd.DataFrame(rows).sort_values("data_quality_score", ascending=False)


def build(args: argparse.Namespace) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    date_key = args.date
    pool_dir = Path(args.fundamental_pool_dir)
    evidence_dir = Path(args.evidence_dir)
    valuation_dir = Path(args.valuation_dir)
    final_layer_dir = Path(args.final_layer_dir)
    backtest_dir = Path(args.historical_backtest_dir)
    financial_dir = Path(args.financial_statements_dir)

    final_score_path, final_score_stale = dated_or_latest_file(evidence_dir, f"final_score_{date_key}.csv", "final_score_*.csv", date_key)
    evidence_quality_path, evidence_quality_stale = dated_or_latest_file(evidence_dir, f"evidence_quality_{date_key}.csv", "evidence_quality_*.csv", date_key)
    valuation_path, valuation_stale = dated_or_latest_file(valuation_dir, f"valuation_score_{date_key}.csv", "valuation_score_*.csv", date_key)

    checks = [
        probe_spot_source(args),
        check_csv_file("fundamental_pool", pool_dir / "current_learning_pool.csv", ["code", "name", "score", "fundamental_score", "price", "amount"], args),
        check_csv_file("final_score", final_score_path, ["code", "name", "final_research_score", "financial_quality_score", "evidence_quality_score"], args, stale=final_score_stale),
        check_csv_file("evidence_quality", evidence_quality_path, ["code", "name", "evidence_quality_score"], args, stale=evidence_quality_stale),
        check_csv_file("valuation_layer", valuation_path, ["code", "name", "valuation_score", "expectation_gap_score"], args, stale=valuation_stale),
        check_csv_file("financial_statements", financial_dir / "current_financial_statement_scores.csv", ["code", "name", "financial_statement_score", "statement_coverage_score"], args),
        check_csv_file("market_v2_score", latest_file(backtest_dir, "market_v2_score_table_*.csv", date_key), ["date", "code", "close", "ret60", "ret120"], args),
    ]
    check_frame = pd.DataFrame(checks)
    critical_checks = {"akshare_spot", "fundamental_pool", "financial_statements"}
    critical_bad = check_frame[(check_frame["check"].isin(critical_checks)) & (check_frame["status"] == "BAD")]
    overall_score = float(check_frame["score"].mean()) if not check_frame.empty else 0.0
    status = "BAD" if not critical_bad.empty or overall_score < 60 else ("WARN" if overall_score < 80 else "OK")

    pool = read_csv(pool_dir / "current_learning_pool.csv")
    fs = read_csv(financial_dir / "current_financial_statement_scores.csv")
    stock_quality = build_stock_quality(pool, fs)
    weak_stock_count = int((stock_quality["data_quality_score"] < 65).sum()) if not stock_quality.empty else 0
    summary = {
        "date": date_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall_score": round(overall_score, 2),
        "status": status,
        "critical_block": bool(not critical_bad.empty),
        "critical_bad_checks": critical_bad["check"].tolist() if not critical_bad.empty else [],
        "weak_stock_count": weak_stock_count,
        "check_count": len(checks),
        "bad_check_count": int((check_frame["status"] == "BAD").sum()) if not check_frame.empty else 0,
        "warn_check_count": int((check_frame["status"] == "WARN").sum()) if not check_frame.empty else 0,
        "notes": "数据质量不足时，基本面优先闸门不会升级为交易候选。",
    }
    return summary, check_frame, stock_quality


def write_outputs(args: argparse.Namespace, summary: dict[str, Any], checks: pd.DataFrame, stock_quality: pd.DataFrame) -> None:
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_key = args.date
    for path in [output_dir / f"data_quality_{date_key}.json", output_dir / "current_data_quality.json"]:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks.to_csv(output_dir / f"data_quality_checks_{date_key}.csv", index=False, encoding="utf-8-sig")
    checks.to_csv(output_dir / "current_data_quality_checks.csv", index=False, encoding="utf-8-sig")
    stock_quality.to_csv(output_dir / f"data_quality_stock_{date_key}.csv", index=False, encoding="utf-8-sig")
    stock_quality.to_csv(output_dir / "current_data_quality_stock.csv", index=False, encoding="utf-8-sig")

    lines = [
        f"# 数据源质量层 V1 - {date_key}",
        "",
        f"- 总体状态：{summary['status']}",
        f"- 总体分：{summary['overall_score']}",
        f"- 严重阻断：{summary['critical_block']}",
        f"- 弱质量股票数：{summary['weak_stock_count']}",
        "",
        "## 数据源检查",
        "| 检查项 | 状态 | 分数 | 行数 | 缺失率 | 提醒 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for _, row in checks.iterrows():
        lines.append(f"| {row['check']} | {row['status']} | {safe_float(row['score'], 0):.2f} | {int(safe_float(row.get('row_count'), 0))} | {safe_float(row.get('missing_rate'), 0):.2%} | {safe_text(row.get('warnings'))} |")
    lines += ["", "## 股票级质量最低的 15 只"]
    if stock_quality.empty:
        lines.append("- 无股票级质量数据。")
    else:
        lines += ["| 代码 | 名称 | 状态 | 分数 | 提醒 |", "|---|---|---|---:|---|"]
        for _, row in stock_quality.sort_values("data_quality_score").head(15).iterrows():
            lines.append(f"| {row['code']} | {row['name']} | {row['data_quality_status']} | {safe_float(row['data_quality_score'], 0):.2f} | {row['data_quality_warnings']} |")
    (report_dir / f"data_quality_{date_key}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    summary, checks, stock_quality = build(args)
    write_outputs(args, summary, checks, stock_quality)
    print(json.dumps({"ok": True, "status": summary["status"], "score": summary["overall_score"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
