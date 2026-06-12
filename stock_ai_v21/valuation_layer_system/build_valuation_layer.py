#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


VALUATION_INDICATORS = ["总市值", "市盈率(TTM)", "市净率", "市现率"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build valuation and expectation-gap layer for the A-share learning pool.")
    parser.add_argument("--learning-pool-dir", default="/app/data/learning_pool")
    parser.add_argument("--evidence-dir", default="/app/data/evidence_hub")
    parser.add_argument("--output-dir", default="/app/data/valuation_layer")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--period", default=os.environ.get("VALUATION_PERIOD", "近三年"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "30")))
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
        if text in {"", "-", "--", "None", "nan"}:
            return default
        number = float(text)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def normalize_code(raw: Any) -> str:
    match = re.search(r"(\d{6})", safe_text(raw))
    return match.group(1) if match else ""


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str})


def call_with_timeout(fn_name: str, kwargs: dict[str, Any], timeout: int) -> tuple[pd.DataFrame, str]:
    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{fn_name} timeout after {timeout}s")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)
    try:
        data = getattr(ak, fn_name)(**kwargs)
        if isinstance(data, pd.DataFrame):
            return data, ""
        return pd.DataFrame(), ""
    except TimeoutError:
        return pd.DataFrame(), f"{fn_name} timeout after {timeout}s"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def latest_and_percentile(df: pd.DataFrame) -> tuple[float, float, int]:
    if df.empty or "value" not in df.columns:
        return math.nan, math.nan, 0
    values = pd.to_numeric(df["value"], errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return math.nan, math.nan, 0
    latest = float(values.iloc[-1])
    percentile = float((values <= latest).mean() * 100)
    return latest, percentile, int(len(values))


def fetch_valuation(code: str, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    row: dict[str, Any] = {}
    errors: list[str] = []
    for indicator in VALUATION_INDICATORS:
        df, error = call_with_timeout(
            "stock_zh_valuation_baidu",
            {"symbol": code, "indicator": indicator, "period": args.period},
            args.timeout,
        )
        if error:
            errors.append(f"{indicator}: {error}")
            continue
        latest, percentile, sample_count = latest_and_percentile(df)
        key = {
            "总市值": "market_cap",
            "市盈率(TTM)": "pe_ttm",
            "市净率": "pb",
            "市现率": "pcf",
        }[indicator]
        row[f"{key}_latest"] = latest
        row[f"{key}_percentile"] = percentile
        row[f"{key}_sample_count"] = sample_count
        time.sleep(0.2)
    return row, errors


def growth_score(financial_row: dict[str, Any]) -> tuple[float, str]:
    revenue_yoy = safe_float(financial_row.get("metric_revenue_yoy"))
    profit_yoy = safe_float(financial_row.get("metric_profit_yoy"))
    ocf = safe_float(financial_row.get("metric_operating_cash_flow"))
    roe = safe_float(financial_row.get("metric_roe"))
    gross_margin = safe_float(financial_row.get("metric_gross_margin"))
    score = 50.0
    notes = []
    if not math.isnan(revenue_yoy):
        score += clamp(revenue_yoy, -40, 60) * 0.22
        notes.append(f"营收同比{revenue_yoy:.2f}%")
    if not math.isnan(profit_yoy):
        score += clamp(profit_yoy, -60, 90) * 0.24
        notes.append(f"利润同比{profit_yoy:.2f}%")
    if not math.isnan(ocf):
        score += 8 if ocf > 0 else -10
        notes.append("经营现金流为正" if ocf > 0 else "经营现金流为负")
    if not math.isnan(roe):
        score += 8 if roe >= 8 else (-6 if roe < 3 else 0)
        notes.append(f"ROE {roe:.2f}%")
    if not math.isnan(gross_margin):
        score += 4 if gross_margin >= 25 else (-4 if gross_margin < 10 else 0)
        notes.append(f"毛利率{gross_margin:.2f}%")
    if not notes:
        score -= 12
        notes.append("财务成长指标缺失")
    return clamp(score), "；".join(notes)


def valuation_score(row: dict[str, Any]) -> tuple[float, str, str, str]:
    percentiles = [
        safe_float(row.get("pe_ttm_percentile")),
        safe_float(row.get("pb_percentile")),
        safe_float(row.get("pcf_percentile")),
    ]
    valid = [value for value in percentiles if not math.isnan(value)]
    if not valid:
        return 45.0, "估值源不足", "估值数据缺失，不能判断贵便宜", "估值接口连续缺失时，不能把该层当作强证据"

    avg_pct = sum(valid) / len(valid)
    high_flags = []
    if safe_float(row.get("pe_ttm_percentile")) >= 85:
        high_flags.append("PE(TTM)历史分位偏高")
    if safe_float(row.get("pb_percentile")) >= 85:
        high_flags.append("PB历史分位偏高")
    if safe_float(row.get("pcf_percentile")) >= 85:
        high_flags.append("PCF历史分位偏高")

    if avg_pct <= 30:
        score = 78
        level = "估值有保护"
    elif avg_pct <= 60:
        score = 63
        level = "估值中性"
    elif avg_pct <= 80:
        score = 48
        level = "估值略贵"
    else:
        score = 32
        level = "估值偏高"

    note = (
        f"PE={safe_float(row.get('pe_ttm_latest')):.2f}/分位{safe_float(row.get('pe_ttm_percentile')):.1f}%；"
        f"PB={safe_float(row.get('pb_latest')):.2f}/分位{safe_float(row.get('pb_percentile')):.1f}%；"
        f"PCF={safe_float(row.get('pcf_latest')):.2f}/分位{safe_float(row.get('pcf_percentile')):.1f}%"
    )
    warning = "；".join(high_flags) if high_flags else "估值未出现明显历史高分位红旗"
    return clamp(score), level, note, warning


def classify_expectation_gap(growth: float, val_score: float, final_score: float, ai_score: float) -> tuple[float, str, str]:
    support = clamp(final_score * 0.7 + ai_score * 0.3)
    gap_score = clamp(growth * 0.42 + val_score * 0.38 + support * 0.20)
    if growth >= 65 and val_score >= 58 and support >= 58:
        label = "正向预期差候选"
        reason = "成长、估值和最终研究层没有明显冲突，值得优先人工验证预期差。"
    elif growth >= 65 and val_score < 45:
        label = "成长强但估值透支"
        reason = "基本面弹性较好，但估值分位偏高，需要等待价格或业绩兑现给安全边际。"
    elif growth < 45 and val_score < 50:
        label = "低质量/高估值风险"
        reason = "成长质量和估值保护都不足，不能靠题材叙事升级。"
    elif support < 50:
        label = "研究层未确认"
        reason = "估值或成长可能有线索，但 AI/最终层尚未形成确认。"
    else:
        label = "中性观察"
        reason = "没有足够强的正向预期差，也没有一票否决的估值信号。"
    return round(gap_score, 2), label, reason


def downgrade_rule(row: dict[str, Any]) -> str:
    rules = []
    if safe_float(row.get("pe_ttm_percentile")) >= 90 or safe_float(row.get("pb_percentile")) >= 90:
        rules.append("估值分位继续处于90%以上且业绩增速没有同步上修")
    if safe_float(row.get("growth_score")) < 45:
        rules.append("下一期营收/利润/现金流继续走弱")
    if safe_float(row.get("ai_score")) < 45:
        rules.append("AI 趋势层维持看空或评分低于45")
    if safe_float(row.get("evidence_quality_score")) < 45:
        rules.append("官方证据/PDF 正文仍无法支撑核心逻辑")
    return "；".join(rules) if rules else "若估值快速抬升但盈利预期没有上修，或 PDF/公告出现订单兑现、现金流、监管问询硬伤，应降级。"


def build_rows(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    date_key = args.date
    learning_df = read_csv(Path(args.learning_pool_dir) / "current_learning_pool.csv")
    final_df = read_csv(Path(args.evidence_dir) / f"final_score_{date_key}.csv")
    financial_df = read_csv(Path(args.evidence_dir) / f"financial_quality_{date_key}.csv")
    if final_df.empty:
        raise SystemExit("missing final_score; run evidence hub first")

    for frame in [learning_df, final_df, financial_df]:
        if not frame.empty and "code" in frame.columns:
            frame["code"] = frame["code"].map(lambda value: normalize_code(value).zfill(6))

    learning_by_code = {row["code"]: row for _, row in learning_df.iterrows()} if not learning_df.empty else {}
    financial_by_code = {row["code"]: row for _, row in financial_df.iterrows()} if not financial_df.empty else {}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for _, final_row in final_df.sort_values("final_research_score", ascending=False).iterrows():
        code = str(final_row["code"]).zfill(6)
        learning_row = learning_by_code.get(code, {})
        financial_row = financial_by_code.get(code, {})
        name = safe_text(final_row.get("name")) or safe_text(learning_row.get("name")) or code
        print(f"[valuation] {code} {name}", flush=True)
        val_row, val_errors = fetch_valuation(code, args)
        for error in val_errors:
            errors.append({"code": code, "source": "stock_zh_valuation_baidu", "error": error})
        g_score, g_note = growth_score(financial_row)
        v_score, v_level, v_note, v_warning = valuation_score(val_row)
        final_score = safe_float(final_row.get("final_research_score"), 50.0)
        ai_score = safe_float(final_row.get("ai_score"), 50.0)
        gap_score, gap_label, gap_reason = classify_expectation_gap(g_score, v_score, final_score, ai_score)
        row = {
            "code": code,
            "name": name,
            "final_research_score": final_score,
            "ai_score": ai_score,
            "financial_quality_score": safe_float(final_row.get("financial_quality_score"), 0.0),
            "evidence_quality_score": safe_float(final_row.get("evidence_quality_score"), 0.0),
            "growth_score": round(g_score, 2),
            "growth_note": g_note,
            "valuation_score": round(v_score, 2),
            "valuation_level": v_level,
            "valuation_note": v_note,
            "valuation_warning": v_warning,
            "expectation_gap_score": gap_score,
            "expectation_gap": gap_label,
            "expectation_gap_reason": gap_reason,
            "downgrade_rule": "",
            "snapshot_price": safe_float(learning_row.get("price"), math.nan),
            **val_row,
        }
        row["downgrade_rule"] = downgrade_rule(row)
        rows.append(row)
        time.sleep(0.3)
    return rows, errors


def write_report(path: Path, rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> None:
    lines = [
        f"# 估值 / 预期差层 V1 - {datetime.now():%Y-%m-%d}",
        "",
        "定位：用近三年估值分位、财务成长质量和最终研究层确认度，判断候选股是“有预期差”还是“估值透支”。该层只用于研究优先级，不是买卖指令。",
        "",
        "| 排名 | 代码 | 名称 | 预期差分 | 预期差判断 | 成长分 | 估值分 | 估值层级 | 降级条件 |",
        "|---:|---|---|---:|---|---:|---:|---|---|",
    ]
    ordered = sorted(rows, key=lambda item: safe_float(item.get("expectation_gap_score")), reverse=True)
    for index, row in enumerate(ordered, start=1):
        lines.append(
            f"| {index} | {row['code']} | {row['name']} | {row['expectation_gap_score']:.2f} | {row['expectation_gap']} | {row['growth_score']:.2f} | {row['valuation_score']:.2f} | {row['valuation_level']} | {row['downgrade_rule']} |"
        )

    lines += ["", "## 单票说明"]
    for row in ordered:
        lines += [
            "",
            f"### {row['name']}({row['code']})",
            f"- 预期差：{row['expectation_gap']}，分数 {row['expectation_gap_score']:.2f}。{row['expectation_gap_reason']}",
            f"- 成长质量：{row['growth_note']}",
            f"- 估值证据：{row['valuation_note']}",
            f"- 估值风险：{row['valuation_warning']}",
            f"- 降级条件：{row['downgrade_rule']}",
        ]
    if errors:
        lines += ["", "## 数据源提醒"]
        for item in errors[:20]:
            lines.append(f"- {item['code']} {item['source']}: {item['error']}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    rows, errors = build_rows(args)
    date_key = args.date
    pd.DataFrame(rows).to_csv(output_dir / f"valuation_score_{date_key}.csv", index=False, encoding="utf-8-sig")
    (output_dir / f"valuation_score_{date_key}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / f"valuation_errors_{date_key}.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_dir / f"valuation_expectation_{date_key}.md", rows, errors)
    print(json.dumps({"ok": True, "rows": len(rows), "errors": len(errors)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
