#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final scoring, trade-plan and review layers.")
    parser.add_argument("--learning-pool-dir", default="/app/data/learning_pool")
    parser.add_argument("--evidence-dir", default="/app/data/evidence_hub")
    parser.add_argument("--valuation-dir", default="/app/data/valuation_layer")
    parser.add_argument("--output-dir", default="/app/data/final_layers")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    return parser.parse_args()


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def safe_float(value: Any, default: float = 0.0) -> float:
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


def normalize_code(raw: Any) -> str:
    match = re.search(r"(\d{6})", safe_text(raw))
    return match.group(1) if match else ""


def load_csv(path: Path, dtype: dict[str, Any] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=dtype)


def latest_previous(path_dir: Path, pattern: str, date_key: str) -> Path | None:
    candidates = sorted(path_dir.glob(pattern))
    previous = [path for path in candidates if date_key not in path.name]
    return previous[-1] if previous else None


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def policy_for(row: pd.Series) -> dict[str, str | float]:
    final_score = safe_float(row.get("final_research_score"))
    ai_score = safe_float(row.get("ai_score"))
    evidence_score = safe_float(row.get("evidence_quality_score"))
    financial_score = safe_float(row.get("financial_quality_score"))
    action = safe_text(row.get("action"))
    trend = safe_text(row.get("ai_trend"))
    ai_advice = safe_text(row.get("ai_advice"))
    valuation_level = safe_text(row.get("valuation_level"))
    expectation_gap = safe_text(row.get("expectation_gap"))
    expectation_gap_score = safe_float(row.get("expectation_gap_score"))

    if action == "等待AI分析" or ai_advice == "未生成":
        return {
            "plan_level": "等待AI分析",
            "cash_policy": "不新开仓；等待 daily_stock_analysis 生成当天 AI 报告后再判断。",
            "buy_rule": "基本面池已入选，但缺少 AI 趋势确认，不能升级为持仓计划。",
            "position_cap": "0",
            "next_check": "等待 18:10 定时分析或手动运行完整 pipeline。",
        }

    negative_ai = ai_score < 45 and (any(word in ai_advice for word in ["卖出", "减仓"]) or any(word in trend for word in ["看空", "下跌"]))
    if action == "降级" and negative_ai:
        return {
            "plan_level": "降级",
            "cash_policy": "不新开仓，不追高；只做资料复盘。",
            "buy_rule": "AI 技术/趋势层偏弱。恢复条件：final>=60、AI>=50、趋势不看空，并且下一次证据包没有新增硬伤。",
            "position_cap": "0",
            "next_check": "复核是否只是短线动量衰减，还是基本面/证据层也在变差。",
        }

    if valuation_level == "估值偏高" or expectation_gap in {"成长强但估值透支", "低质量/高估值风险"}:
        return {
            "plan_level": "观察",
            "cash_policy": "不主动新开仓；估值/预期差层提示安全边际不足。",
            "buy_rule": "只有估值分位回落、业绩预期上修，且 final>=65、AI>=50 时再升级。",
            "position_cap": "0",
            "next_check": "重点复核估值分位、业绩兑现和公告/PDF 是否支持市场预期。",
        }

    if action == "优先深挖" and final_score >= 72 and ai_score >= 55:
        return {
            "plan_level": "买入前观察",
            "cash_policy": "允许小仓试错观察，单票风险预算不超过账户权益 0.5%-1%。",
            "buy_rule": "只在回踩不破关键价且 AI 趋势不转弱时考虑；突破必须放量确认。",
            "position_cap": "试错仓",
            "next_check": "核对最新公告、财务现金流和价格是否维持多头结构。",
        }
    if action in {"观察", "证据不足"} or final_score >= 58:
        reason = "证据不足，先补材料。" if evidence_score < 45 else "综合层仍未形成强共振。"
        if expectation_gap_score >= 70:
            reason = "预期差层较强，但交易/趋势确认还不够。"
        return {
            "plan_level": "观察",
            "cash_policy": "不主动新开仓，可放入观察池等待二次确认。",
            "buy_rule": f"{reason} 只有 final>=65、AI>=50 且趋势不看空时再升级。",
            "position_cap": "0",
            "next_check": "等待下一次评分、公告证据或趋势修复。",
        }
    if ai_score < 45 and ("看空" in trend or ai_advice in {"卖出", "减仓", "观望"}):
        detail = "AI 技术/趋势层偏弱"
    elif financial_score < 45:
        detail = "财务质量偏弱"
    elif evidence_score < 45:
        detail = "证据覆盖不足"
    else:
        detail = "最终分不足"
    return {
        "plan_level": "降级",
        "cash_policy": "不新开仓，不追高；只做资料复盘。",
        "buy_rule": f"{detail}。恢复条件：final>=60、AI>=50、趋势不看空，并且下一次证据包没有新增硬伤。",
        "position_cap": "0",
        "next_check": "复核是否只是短线动量衰减，还是基本面/证据层也在变差。",
    }


def merge_inputs(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[str]]:
    date_key = args.date
    learning_dir = Path(args.learning_pool_dir)
    evidence_dir = Path(args.evidence_dir)
    report_dir = Path(args.report_dir)
    selected_plan_path = learning_dir / f"selected_{date_key}.json"
    final_path = evidence_dir / f"final_score_{date_key}.csv"
    learning_path = learning_dir / "current_learning_pool.csv"
    evidence_quality_path = evidence_dir / f"evidence_quality_{date_key}.csv"
    financial_path = evidence_dir / f"financial_quality_{date_key}.csv"
    valuation_path = Path(args.valuation_dir) / f"valuation_score_{date_key}.csv"
    source_errors_path = evidence_dir / "raw_events" / f"source_errors_{date_key}.json"

    final_df = load_csv(final_path, dtype={"code": str})
    learning_df = load_csv(learning_path, dtype={"code": str})
    evidence_df = load_csv(evidence_quality_path, dtype={"code": str})
    financial_df = load_csv(financial_path, dtype={"code": str})
    valuation_df = load_csv(valuation_path, dtype={"code": str})
    selected_plans = read_json(selected_plan_path) or []
    source_errors = read_json(source_errors_path) or []

    missing = []
    for label, path in [
        ("final_score", final_path),
        ("current_learning_pool", learning_path),
        ("selected_plan", selected_plan_path),
        ("evidence_quality", evidence_quality_path),
        ("financial_quality", financial_path),
        ("valuation_expectation", valuation_path),
        ("daily_stock_analysis_report", report_dir / f"report_{date_key}.md"),
    ]:
        if not path.exists():
            missing.append(f"{label}: {path}")
    if final_df.empty:
        raise SystemExit("missing or empty final_score; run evidence hub first")

    for frame in [final_df, learning_df, evidence_df, financial_df, valuation_df]:
        if not frame.empty and "code" in frame.columns:
            frame["code"] = frame["code"].map(lambda value: normalize_code(value).zfill(6))
    return final_df, learning_df, evidence_df, valuation_df, selected_plans, source_errors + [{"missing": item} for item in missing]


def build_trade_rows(final_df: pd.DataFrame, learning_df: pd.DataFrame, valuation_df: pd.DataFrame, selected_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    learning_by_code = {str(row["code"]).zfill(6): row for _, row in learning_df.iterrows()} if not learning_df.empty else {}
    valuation_by_code = {str(row["code"]).zfill(6): row for _, row in valuation_df.iterrows()} if not valuation_df.empty else {}
    plan_by_code = {str(item.get("code", "")).zfill(6): item for item in selected_plans}
    rows: list[dict[str, Any]] = []
    for _, row in final_df.sort_values("final_research_score", ascending=False).iterrows():
        code = str(row["code"]).zfill(6)
        name = safe_text(row.get("name")) or safe_text(learning_by_code.get(code, {}).get("name")) or code
        plan = plan_by_code.get(code, {})
        learning_row = learning_by_code.get(code)
        valuation_row = valuation_by_code.get(code)
        decision_row = row.to_dict()
        if valuation_row is not None:
            decision_row.update(valuation_row.to_dict())
        policy = policy_for(decision_row)
        current_price = safe_float(plan.get("snapshot_price"), safe_float(learning_row.get("price") if learning_row is not None else 0))
        entry_low = safe_float(plan.get("entry_low"), current_price * 0.97)
        entry_high = safe_float(plan.get("entry_high"), current_price * 0.995)
        breakout = safe_float(plan.get("breakout_trigger"), current_price * 1.025)
        stop = safe_float(plan.get("risk_stop"), current_price * 0.92)
        rows.append(
            {
                "code": code,
                "name": name,
                "final_research_score": safe_float(row.get("final_research_score")),
                "learning_score": safe_float(row.get("learning_score")),
                "ai_score": safe_float(row.get("ai_score")),
                "financial_quality_score": safe_float(row.get("financial_quality_score")),
                "evidence_quality_score": safe_float(row.get("evidence_quality_score")),
                "action": safe_text(row.get("action")),
                "reason": safe_text(row.get("reason")),
                "ai_advice": safe_text(row.get("ai_advice")),
                "ai_trend": safe_text(row.get("ai_trend")),
                "valuation_score": safe_float(valuation_row.get("valuation_score") if valuation_row is not None else math.nan),
                "valuation_level": safe_text(valuation_row.get("valuation_level") if valuation_row is not None else "缺失"),
                "expectation_gap_score": safe_float(valuation_row.get("expectation_gap_score") if valuation_row is not None else math.nan),
                "expectation_gap": safe_text(valuation_row.get("expectation_gap") if valuation_row is not None else "缺失"),
                "expectation_gap_reason": safe_text(valuation_row.get("expectation_gap_reason") if valuation_row is not None else ""),
                "downgrade_rule": safe_text(valuation_row.get("downgrade_rule") if valuation_row is not None else ""),
                "plan_level": policy["plan_level"],
                "cash_policy": policy["cash_policy"],
                "buy_rule": policy["buy_rule"],
                "position_cap": policy["position_cap"],
                "next_check": policy["next_check"],
                "snapshot_price": round(current_price, 2),
                "entry_low": round(entry_low, 2),
                "entry_high": round(entry_high, 2),
                "breakout_trigger": round(breakout, 2),
                "risk_stop": round(stop, 2),
            }
        )
    return rows


def write_trade_plan(report_path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        f"# A股 AI 最终交易计划层 V2 - {datetime.now():%Y-%m-%d}",
        "",
        "定位：把基本面池、AI 分析、财务质量、官方证据层和估值/预期差层合并后，给出“观察/降级/买入前观察”的规则化计划。它不是自动买卖指令。",
        "",
        "| 排名 | 代码 | 名称 | 最终分 | 预期差 | 层级 | 仓位规则 | 触发/恢复条件 | 风控价 |",
        "|---:|---|---|---:|---|---|---|---|---:|",
    ]
    for index, row in enumerate(rows, start=1):
        trigger_text = row["buy_rule"]
        if row["plan_level"] == "买入前观察":
            trigger_text = f"回踩 {row['entry_low']:.2f}-{row['entry_high']:.2f} 或放量站上 {row['breakout_trigger']:.2f}；{trigger_text}"
        lines.append(
            f"| {index} | {row['code']} | {row['name']} | {row['final_research_score']:.2f} | {row['expectation_gap']}({row['expectation_gap_score']:.2f}) | {row['plan_level']} | {row['cash_policy']} | {trigger_text} | {row['risk_stop']:.2f} |"
        )

    lines += ["", "## 单票细则"]
    for row in rows:
        lines += [
            "",
            f"### {row['name']}({row['code']})",
            f"- 当前结论：{row['plan_level']}，原始动作：{row['action']}。",
            f"- 分数拆解：基本面池 {row['learning_score']:.2f}，AI {row['ai_score']:.2f}，财务 {row['financial_quality_score']:.2f}，证据 {row['evidence_quality_score']:.2f}。",
            f"- 估值/预期差：{row['valuation_level']}，{row['expectation_gap']}，预期差分 {row['expectation_gap_score']:.2f}。{row['expectation_gap_reason']}",
            f"- AI 结论：{row['ai_advice']} / {row['ai_trend']}。",
            f"- 排序理由：{row['reason']}",
            f"- 降级条件：{row['downgrade_rule'] or '等待下一次估值、公告和财务数据交叉验证。'}",
            f"- 下一次检查：{row['next_check']}",
        ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_review(report_path: Path, output_dir: Path, rows: list[dict[str, Any]], learning_df: pd.DataFrame, date_key: str) -> None:
    previous_path = latest_previous(output_dir, "final_trade_plan_*.json", date_key)
    current_by_code = {row["code"]: row for row in rows}
    learning_by_code = {str(row["code"]).zfill(6): row for _, row in learning_df.iterrows()} if not learning_df.empty else {}
    lines = [
        f"# A股 AI 最终复盘层 V1 - {datetime.now():%Y-%m-%d}",
        "",
    ]
    if previous_path is None:
        lines += [
            "暂无上一期最终交易计划，本次建立 V1 基线。",
            "",
            "## 今日需要人工盯的变化",
        ]
        for row in rows:
            lines.append(f"- {row['name']}({row['code']})：{row['plan_level']}；最终分 {row['final_research_score']:.2f}；{row['reason']}")
        report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return

    previous_rows = read_json(previous_path) or []
    lines += [f"对比上一期：`{previous_path.name}`", "", "| 代码 | 名称 | 计划变化 | 分数变化 | 今日状态 |", "|---|---|---|---:|---|"]
    seen = set()
    for old in previous_rows:
        code = str(old.get("code", "")).zfill(6)
        seen.add(code)
        new = current_by_code.get(code)
        if not new:
            lines.append(f"| {code} | {safe_text(old.get('name'))} | 移出最终名单 | - | 今日未进入 Top 列表，降级为资料复查 |")
            continue
        old_level = safe_text(old.get("plan_level"))
        new_level = safe_text(new.get("plan_level"))
        old_score = safe_float(old.get("final_research_score"))
        new_score = safe_float(new.get("final_research_score"))
        price_row = learning_by_code.get(code)
        current_price = safe_float(price_row.get("price") if price_row is not None else new.get("snapshot_price"))
        high = safe_float(price_row.get("high") if price_row is not None else 0)
        low = safe_float(price_row.get("low") if price_row is not None else 0)
        trigger = safe_float(old.get("breakout_trigger"))
        stop = safe_float(old.get("risk_stop"))
        if stop and low and low <= stop:
            status = "触发风控价，维持/加重降级"
        elif trigger and high and high >= trigger:
            status = "触发突破观察，但仍需看最终层是否支持"
        else:
            status = f"现价 {current_price:.2f}，未触发硬条件"
        change = "无变化" if old_level == new_level else f"{old_level} -> {new_level}"
        lines.append(f"| {code} | {new['name']} | {change} | {new_score - old_score:+.2f} | {status} |")

    for code, new in current_by_code.items():
        if code not in seen:
            lines.append(f"| {code} | {new['name']} | 新进入最终名单 | + | {new['plan_level']} |")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_status(report_path: Path, args: argparse.Namespace, rows: list[dict[str, Any]], source_errors: list[dict[str, Any]]) -> None:
    date_key = args.date
    waiting_ai = any(row.get("plan_level") == "等待AI分析" for row in rows)
    checks = [
        ("AKShare 生成基本面池", Path(args.learning_pool_dir) / "current_learning_pool.csv"),
        ("基本面池股票列表喂给 daily_stock_analysis", Path(args.learning_pool_dir) / "current_stock_list.txt"),
        ("daily_stock_analysis AI 分析报告", Path(args.report_dir) / f"report_{date_key}.md"),
        ("评分层 final_score", Path(args.evidence_dir) / f"final_score_{date_key}.csv"),
        ("估值/预期差层", Path(args.valuation_dir) / f"valuation_score_{date_key}.csv"),
        ("交易计划层", Path(args.report_dir) / f"final_trade_plan_{date_key}.md"),
        ("复盘层", Path(args.report_dir) / f"final_review_{date_key}.md"),
    ]
    lines = [
        f"# A股 AI 系统闭环状态 V1 - {datetime.now():%Y-%m-%d}",
        "",
        "| 模块 | 状态 | 文件 |",
        "|---|---|---|",
    ]
    for name, path in checks:
        if name == "daily_stock_analysis AI 分析报告" and waiting_ai:
            status = "⚠️ 报告存在，但待覆盖基本面池新名单"
        else:
            status = "✅ 已闭环" if path.exists() and path.stat().st_size > 0 else "❌ 缺失"
        lines.append(f"| {name} | {status} | `{path}` |")

    lines += [
        "",
        "## 今日最终动作统计",
    ]
    stats: dict[str, int] = {}
    for row in rows:
        stats[row["plan_level"]] = stats.get(row["plan_level"], 0) + 1
    for key, value in sorted(stats.items()):
        lines.append(f"- {key}: {value} 只")

    if source_errors:
        lines += ["", "## 数据源提醒"]
        grouped: dict[str, int] = {}
        examples: list[dict[str, Any]] = []
        for item in source_errors:
            if "missing" in item:
                grouped["missing"] = grouped.get("missing", 0) + 1
                examples.append(item)
                continue
            key = safe_text(item.get("source")) or "unknown_source"
            grouped[key] = grouped.get(key, 0) + 1
            if len(examples) < 8:
                examples.append(item)
        for key, count in sorted(grouped.items()):
            lines.append(f"- {key}: {count} 条提醒")
        if examples:
            lines += ["", "### 样例"]
            for item in examples[:8]:
                if "missing" in item:
                    lines.append(f"- 缺失：{item['missing']}")
                else:
                    error = safe_text(item.get("error", item))
                    if len(error) > 160:
                        error = error[:160] + "..."
                    lines.append(f"- {item.get('code', '')} {item.get('source', '')}: {error}")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    date_key = args.date
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    final_df, learning_df, evidence_df, valuation_df, selected_plans, source_errors = merge_inputs(args)
    rows = build_trade_rows(final_df, learning_df, valuation_df, selected_plans)

    trade_json = output_dir / f"final_trade_plan_{date_key}.json"
    current_json = output_dir / "current_final_trade_plan.json"
    trade_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    final_plan_report = report_dir / f"final_trade_plan_{date_key}.md"
    final_review_report = report_dir / f"final_review_{date_key}.md"
    status_report = report_dir / f"system_status_{date_key}.md"
    write_trade_plan(final_plan_report, rows)
    write_review(final_review_report, output_dir, rows, learning_df, date_key)
    write_status(status_report, args, rows, source_errors)

    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "rows": len(rows),
                "reports": [str(final_plan_report), str(final_review_report), str(status_report)],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
