#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


HORIZONS = [30, 60, 90]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build 30/60/90 day holding review scores.")
    parser.add_argument("--final-layer-dir", default="/app/data/final_layers")
    parser.add_argument("--learning-pool-dir", default="/app/data/learning_pool")
    parser.add_argument("--output-dir", default="/app/data/holding_review")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--timeout", type=int, default=35)
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


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def date_from_name(path: Path) -> datetime | None:
    match = re.search(r"(\d{8})", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def call_spot(timeout: int) -> tuple[pd.DataFrame, list[str]]:
    errors = []

    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"spot timeout after {timeout}s")

    for fn_name in ("stock_zh_a_spot", "stock_zh_a_spot_em"):
        previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
        try:
            df = getattr(ak, fn_name)()
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df, errors
            errors.append(f"{fn_name}: empty dataframe")
        except Exception as exc:
            errors.append(f"{fn_name}: {type(exc).__name__}: {str(exc)[:240]}")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
    return pd.DataFrame(), errors


def normalize_spot(raw: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if raw.empty:
        return {}
    code_col = pick_column(raw, ["代码", "code", "股票代码"])
    name_col = pick_column(raw, ["名称", "name", "股票名称"])
    price_col = pick_column(raw, ["最新价", "最新", "现价", "trade", "close"])
    high_col = pick_column(raw, ["最高", "最高价", "high"])
    low_col = pick_column(raw, ["最低", "最低价", "low"])
    if code_col is None or price_col is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in raw.iterrows():
        code = normalize_code(row.get(code_col)).zfill(6)
        if not code:
            continue
        out[code] = {
            "code": code,
            "name": safe_text(row.get(name_col)) if name_col else code,
            "price": safe_float(row.get(price_col)),
            "high": safe_float(row.get(high_col)) if high_col else math.nan,
            "low": safe_float(row.get(low_col)) if low_col else math.nan,
        }
    return out


def current_rows(final_layer_dir: Path, date_key: str) -> list[dict[str, Any]]:
    path = final_layer_dir / f"final_trade_plan_{date_key}.json"
    rows = read_json(path)
    if rows:
        return rows
    rows = read_json(final_layer_dir / "current_final_trade_plan.json")
    return rows or []


def history_file_for(final_layer_dir: Path, current_dt: datetime, horizon: int) -> Path | None:
    candidates = []
    for path in sorted(final_layer_dir.glob("final_trade_plan_*.json")):
        path_dt = date_from_name(path)
        if path_dt is None or path_dt >= current_dt:
            continue
        age = (current_dt - path_dt).days
        if age >= horizon:
            candidates.append((abs(age - horizon), path_dt, path))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], -item[1].timestamp()))[0][2]


def score_review(old: dict[str, Any], new: dict[str, Any] | None, quote: dict[str, Any] | None, horizon: int) -> dict[str, Any]:
    code = str(old.get("code", "")).zfill(6)
    name = safe_text((new or old).get("name")) or code
    entry_price = safe_float(old.get("snapshot_price"))
    current_price = safe_float((quote or {}).get("price"), safe_float((new or {}).get("snapshot_price"), entry_price))
    return_pct = (current_price / entry_price - 1) * 100 if entry_price and not math.isnan(entry_price) and not math.isnan(current_price) else math.nan
    old_score = safe_float(old.get("final_research_score"), 50.0)
    new_score = safe_float((new or {}).get("final_research_score"), math.nan)
    score_change = new_score - old_score if not math.isnan(new_score) else math.nan
    old_level = safe_text(old.get("plan_level"))
    new_level = safe_text((new or {}).get("plan_level")) if new else "移出最终名单"

    review_score = 50.0
    if not math.isnan(return_pct):
        review_score += clamp(return_pct, -25, 35) * 1.2
    if not math.isnan(score_change):
        review_score += clamp(score_change, -25, 25) * 0.9
    if new_level in {"买入前观察", "优先深挖"}:
        review_score += 8
    elif new_level == "观察":
        review_score += 2
    elif new_level in {"降级", "移出最终名单"}:
        review_score -= 12

    if math.isnan(return_pct):
        decision = "数据不足"
        reason = "缺少入选价或当前价，先补行情数据。"
    elif return_pct <= -12:
        decision = "降级复核"
        reason = "持有窗口跌幅超过 -12%，需要复核逻辑是否失效。"
    elif new_level in {"降级", "移出最终名单"}:
        decision = "退出观察"
        reason = "当前最终层已经降级或移出，不能继续按原计划持有。"
    elif not math.isnan(score_change) and score_change <= -10:
        decision = "降级复核"
        reason = "研究分较入选日下滑超过 10 分。"
    elif return_pct >= 20 and review_score >= 70:
        decision = "兑现/上移风控"
        reason = "持有窗口收益较高，优先保护利润而不是追加追高。"
    elif review_score >= 62:
        decision = "继续跟踪"
        reason = "收益、研究分和当前层级未出现明显破坏。"
    else:
        decision = "中性观察"
        reason = "未触发硬性退出，也未形成升级信号。"

    return {
        "horizon_days": horizon,
        "code": code,
        "name": name,
        "old_plan_level": old_level,
        "current_plan_level": new_level,
        "entry_price": round(entry_price, 3) if not math.isnan(entry_price) else "",
        "current_price": round(current_price, 3) if not math.isnan(current_price) else "",
        "return_pct": round(return_pct, 2) if not math.isnan(return_pct) else "",
        "old_score": round(old_score, 2),
        "current_score": round(new_score, 2) if not math.isnan(new_score) else "",
        "score_change": round(score_change, 2) if not math.isnan(score_change) else "",
        "review_score": round(clamp(review_score), 2),
        "review_decision": decision,
        "review_reason": reason,
    }


def build_reviews(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    date_key = args.date
    current_dt = datetime.strptime(date_key, "%Y%m%d")
    final_layer_dir = Path(args.final_layer_dir)
    current = current_rows(final_layer_dir, date_key)
    current_by_code = {str(row.get("code", "")).zfill(6): row for row in current}
    history_by_horizon = {horizon: history_file_for(final_layer_dir, current_dt, horizon) for horizon in HORIZONS}
    needs_quotes = any(path is not None for path in history_by_horizon.values())
    if needs_quotes:
        spot, errors = call_spot(args.timeout)
        quotes = normalize_spot(spot)
    else:
        errors = []
        quotes = {}

    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        history_path = history_by_horizon[horizon]
        if history_path is None:
            rows.append(
                {
                    "horizon_days": horizon,
                    "code": "",
                    "name": "",
                    "review_decision": "等待样本",
                    "review_reason": f"暂无满 {horizon} 天的历史最终交易计划；今天继续积累基线。",
                    "review_score": "",
                }
            )
            continue
        old_rows = read_json(history_path) or []
        for old in old_rows:
            code = str(old.get("code", "")).zfill(6)
            rows.append(score_review(old, current_by_code.get(code), quotes.get(code), horizon))
    return rows, errors


def write_report(path: Path, rows: list[dict[str, Any]], errors: list[str]) -> None:
    lines = [
        f"# 30/60/90 天持仓复盘打分 V1 - {datetime.now():%Y-%m-%d}",
        "",
        "定位：复盘的是“AI 研究计划入池后的表现”，不是自动交易账户盈亏。满 30/60/90 天后，系统会按入选价、当前价、最终研究分变化和当前层级打分。",
        "",
        "| 周期 | 代码 | 名称 | 复盘分 | 结论 | 区间收益 | 分数变化 | 层级变化 | 原因 |",
        "|---:|---|---|---:|---|---:|---:|---|---|",
    ]
    for row in rows:
        if not row.get("code"):
            lines.append(f"| {row['horizon_days']} | - | - | - | {row['review_decision']} | - | - | - | {row['review_reason']} |")
            continue
        level_change = f"{row.get('old_plan_level', '')} -> {row.get('current_plan_level', '')}"
        lines.append(
            f"| {row['horizon_days']} | {row['code']} | {row['name']} | {row['review_score']} | {row['review_decision']} | {row['return_pct']} | {row['score_change']} | {level_change} | {row['review_reason']} |"
        )
    lines += [
        "",
        "## 复盘规则",
        "- 跌幅超过 -12%、最终层降级/移出、研究分下降超过 10 分，优先进入降级复核。",
        "- 收益超过 20% 且复盘分较高，优先考虑兑现或上移风控，而不是无条件加仓。",
        "- 未满 30/60/90 天时只建立基线，不强行评价系统好坏。",
    ]
    if errors:
        lines += ["", "## 行情数据源提醒"]
        for error in errors[:8]:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    rows, errors = build_reviews(args)
    date_key = args.date
    pd.DataFrame(rows).to_csv(output_dir / f"holding_review_{date_key}.csv", index=False, encoding="utf-8-sig")
    (output_dir / f"holding_review_{date_key}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_dir / f"holding_review_{date_key}.md", rows, errors)
    print(json.dumps({"ok": True, "rows": len(rows), "errors": len(errors)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
