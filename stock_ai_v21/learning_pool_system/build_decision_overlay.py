#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge learning-pool scores with AI report decisions.")
    parser.add_argument("--data-dir", default="/app/data/learning_pool")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    return parser.parse_args()


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_ai_summary(report_path: Path) -> dict[str, dict[str, str]]:
    if not report_path.exists():
        return {}
    text = report_path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(r"\*\*(?P<name>.+?)\((?P<code>\d{6})\)\*\*:\s*(?P<advice>[^|]+)\|\s*评分\s*(?P<score>\d+)\s*\|\s*(?P<trend>.+)")
    result: dict[str, dict[str, str]] = {}
    for match in pattern.finditer(text):
        code = match.group("code")
        result[code] = {
            "name": match.group("name").strip(),
            "advice": match.group("advice").strip(),
            "ai_score": match.group("score").strip(),
            "trend": match.group("trend").strip(),
        }
    return result


def action_for(learning_score: float, ai_score: float, advice: str, trend: str) -> tuple[str, str]:
    negative = any(word in advice for word in ["卖出", "减仓"]) or any(word in trend for word in ["看空", "下跌"])
    if negative and ai_score < 45:
        return "降级", "基础池评分高，但 AI 技术/趋势层转弱；不追高，等待趋势修复。"
    if learning_score >= 75 and ai_score >= 55:
        return "优先复核", "规则层和 AI 层未明显冲突，可进入产业链/财报/公告深挖。"
    if ai_score < 40:
        return "剔除/仅复盘", "AI 评分偏低，除非次日重新站回关键均线，否则不进入买入计划。"
    return "观察", "保留在观察池，等待量价和均线结构确认。"


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir)
    report_dir = Path(args.report_dir)
    date_key = args.date
    pool_path = data_dir / "current_learning_pool.csv"
    stock_list_path = data_dir / "current_stock_list.txt"
    report_path = report_dir / f"report_{date_key}.md"
    output_path = report_dir / f"decision_overlay_{date_key}.md"

    if not pool_path.exists():
        raise SystemExit(f"missing learning pool: {pool_path}")
    pool = pd.read_csv(pool_path, dtype={"code": str})
    selected_codes = []
    if stock_list_path.exists():
        selected_codes = [code.strip() for code in stock_list_path.read_text(encoding="utf-8").split(",") if code.strip()]
    if selected_codes:
        pool = pool[pool["code"].isin(selected_codes)].copy()
    pool = pool.sort_values("rank")
    ai = parse_ai_summary(report_path)

    lines = [
        f"# A股 AI 决策复核层 - {datetime.now():%Y-%m-%d}",
        "",
        "定位：把基本面池规则评分和 daily_stock_analysis 的 AI 结论合并，解决“基础池选出但 AI 不支持”的冲突。",
        "",
        "| 代码 | 名称 | 基础池分 | 基础池层级 | AI建议 | AI评分 | AI趋势 | 复核动作 | 理由 |",
        "|---|---|---:|---|---|---:|---|---|---|",
    ]

    for _, row in pool.iterrows():
        code = str(row["code"]).zfill(6)
        learning_score = safe_float(row.get("score"))
        ai_row = ai.get(code, {})
        advice = ai_row.get("advice", "未生成")
        ai_score = safe_float(ai_row.get("ai_score"))
        trend = ai_row.get("trend", "未知")
        action, reason = action_for(learning_score, ai_score, advice, trend)
        lines.append(
            "| "
            + " | ".join(
                [
                    code,
                    str(row.get("name", "")),
                    f"{learning_score:.2f}",
                    str(row.get("lane", "")),
                    advice,
                    f"{ai_score:.0f}",
                    trend,
                    action,
                    reason,
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## 使用规则",
        "- 基础池分高但 AI 评分低：优先视为“基本面线索强、交易确认不足”，不直接进入买入计划。",
        "- AI 给出卖出/减仓且评分低于 45：自动降级。",
        "- 只有规则层和 AI 层同时支持，才进入下一步产业链、公告和财务质量深挖。",
        "",
        "## 当前数据缺口",
        "- 公共 SearXNG 搜索源大量限流，公告/新闻证据不足。",
        "- 筹码分布接口多次失败，筹码层暂不作为硬判断。",
    ]
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
