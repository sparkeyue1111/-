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
    parser = argparse.ArgumentParser(description="Generate A-share learning pool and feed daily_stock_analysis.")
    parser.add_argument("--env-file", default="/app/data/app.env")
    parser.add_argument("--output-dir", default="/app/data/learning_pool")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--pool-size", type=int, default=50)
    parser.add_argument("--analyze-count", type=int, default=5)
    parser.add_argument("--min-amount", type=float, default=200_000_000)
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--stock-list-key", default="STOCK_LIST")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_cn() -> datetime:
    return datetime.now()


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
            if value in {"", "-", "--", "None", "nan"}:
                return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except Exception:
        return default


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def normalize_code(raw_code: Any) -> str:
    text = str(raw_code or "").strip().lower()
    match = re.search(r"(\d{6})$", text)
    return match.group(1) if match else ""


def is_main_a_share(code: str) -> bool:
    return bool(re.fullmatch(r"[036]\d{5}", code))


def load_spot_data() -> tuple[pd.DataFrame, str, list[str]]:
    errors: list[str] = []
    for fn_name in ("stock_zh_a_spot", "stock_zh_a_spot_em"):
        try:
            df = getattr(ak, fn_name)()
            if df is not None and not df.empty:
                return df, fn_name, errors
            errors.append(f"{fn_name}: empty dataframe")
        except Exception as exc:
            errors.append(f"{fn_name}: {type(exc).__name__}: {str(exc)[:240]}")
    raise RuntimeError("; ".join(errors))


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def normalized_frame(raw: pd.DataFrame) -> pd.DataFrame:
    col = {
        "raw_code": pick_column(raw, ["代码", "code", "股票代码"]),
        "name": pick_column(raw, ["名称", "name", "股票名称"]),
        "price": pick_column(raw, ["最新价", "最新", "现价", "close"]),
        "pct_chg": pick_column(raw, ["涨跌幅", "涨幅", "changepercent", "pct_chg"]),
        "change": pick_column(raw, ["涨跌额", "涨跌", "change"]),
        "open": pick_column(raw, ["今开", "开盘", "open"]),
        "high": pick_column(raw, ["最高", "最高价", "high"]),
        "low": pick_column(raw, ["最低", "最低价", "low"]),
        "prev_close": pick_column(raw, ["昨收", "昨收价", "preclose"]),
        "volume": pick_column(raw, ["成交量", "volume"]),
        "amount": pick_column(raw, ["成交额", "amount"]),
        "turnover_rate": pick_column(raw, ["换手率", "turnover"]),
        "volume_ratio": pick_column(raw, ["量比"]),
        "pe": pick_column(raw, ["市盈率-动态", "市盈率", "pe"]),
        "pb": pick_column(raw, ["市净率", "pb"]),
        "total_mv": pick_column(raw, ["总市值", "总市值-元", "总市值(元)"]),
        "circ_mv": pick_column(raw, ["流通市值", "流通市值-元", "流通市值(元)"]),
        "pct_60d": pick_column(raw, ["60日涨跌幅"]),
        "pct_ytd": pick_column(raw, ["年初至今涨跌幅"]),
        "timestamp": pick_column(raw, ["时间戳", "时间", "timestamp"]),
    }
    required = ["raw_code", "name", "price", "pct_chg", "amount"]
    missing = [key for key in required if col[key] is None]
    if missing:
        raise RuntimeError(f"行情数据缺少必要字段: {missing}; columns={list(raw.columns)}")

    out = pd.DataFrame()
    out["code"] = raw[col["raw_code"]].map(normalize_code)
    out["name"] = raw[col["name"]].astype(str).str.strip()
    numeric_fields = [
        "price",
        "pct_chg",
        "change",
        "open",
        "high",
        "low",
        "prev_close",
        "volume",
        "amount",
        "turnover_rate",
        "volume_ratio",
        "pe",
        "pb",
        "total_mv",
        "circ_mv",
        "pct_60d",
        "pct_ytd",
    ]
    for field in numeric_fields:
        source = col.get(field)
        if source is None:
            out[field] = None
        else:
            out[field] = pd.to_numeric(
                raw[source].astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
                errors="coerce",
            )
    source = col.get("timestamp")
    out["timestamp"] = raw[source].astype(str) if source else ""
    return out


def metric_scores(row: pd.Series) -> dict[str, float]:
    price = safe_float(row.get("price"))
    pct = safe_float(row.get("pct_chg"))
    amount = safe_float(row.get("amount"))
    high = safe_float(row.get("high"), price)
    low = safe_float(row.get("low"), price)
    prev_close = safe_float(row.get("prev_close"), price)
    turnover = safe_float(row.get("turnover_rate"), 0)
    vol_ratio = safe_float(row.get("volume_ratio"), 0)
    pct_60d = safe_float(row.get("pct_60d"), 0)
    pct_ytd = safe_float(row.get("pct_ytd"), 0)

    amount_score = clamp(25 + 28 * math.log10(max(amount, 1) / 100_000_000))
    if amount >= 1_000_000_000:
        amount_score = max(amount_score, 82)
    if amount >= 3_000_000_000:
        amount_score = max(amount_score, 92)

    if pct < -7:
        momentum_score = 18
    elif pct < 0:
        momentum_score = 42 + pct * 3
    elif pct <= 3:
        momentum_score = 56 + pct * 8
    elif pct <= 7:
        momentum_score = 80 - (pct - 3) * 2
    else:
        momentum_score = 68 - (pct - 7) * 7
    momentum_score = clamp(momentum_score)

    if high > low:
        close_location = clamp((price - low) / (high - low) * 100)
    else:
        close_location = 50.0

    amplitude = abs(high - low) / prev_close * 100 if prev_close > 0 else 0
    if amplitude <= 1:
        volatility_score = 42
    elif amplitude <= 6:
        volatility_score = 58 + amplitude * 5
    elif amplitude <= 10:
        volatility_score = 86 - (amplitude - 6) * 5
    else:
        volatility_score = 62 - (amplitude - 10) * 5
    volatility_score = clamp(volatility_score)

    trend_score = 50.0
    if row.get("pct_60d") is not None and not pd.isna(row.get("pct_60d")):
        trend_score = clamp(50 + pct_60d * 0.8)
    if row.get("pct_ytd") is not None and not pd.isna(row.get("pct_ytd")):
        trend_score = clamp(trend_score * 0.7 + clamp(50 + pct_ytd * 0.5) * 0.3)

    if turnover <= 0:
        turnover_score = 50.0
    elif turnover <= 2:
        turnover_score = 45 + turnover * 10
    elif turnover <= 8:
        turnover_score = 80
    elif turnover <= 15:
        turnover_score = 80 - (turnover - 8) * 4
    else:
        turnover_score = 42
    turnover_score = clamp(turnover_score)

    if vol_ratio <= 0:
        volume_ratio_score = 50.0
    elif vol_ratio <= 1:
        volume_ratio_score = 45 + vol_ratio * 15
    elif vol_ratio <= 2.5:
        volume_ratio_score = 72 + (vol_ratio - 1) * 8
    elif vol_ratio <= 5:
        volume_ratio_score = 82 - (vol_ratio - 2.5) * 8
    else:
        volume_ratio_score = 48
    volume_ratio_score = clamp(volume_ratio_score)

    risk_penalty = 0.0
    if amount < 300_000_000:
        risk_penalty += 8
    if price < 5:
        risk_penalty += 5
    if pct > 9.5:
        risk_penalty += 12
    if pct < -7:
        risk_penalty += 12
    if amplitude > 12:
        risk_penalty += 8

    final = (
        amount_score * 0.30
        + momentum_score * 0.25
        + close_location * 0.15
        + volatility_score * 0.10
        + trend_score * 0.10
        + turnover_score * 0.05
        + volume_ratio_score * 0.05
        - risk_penalty
    )

    return {
        "score": round(clamp(final), 2),
        "liquidity_score": round(amount_score, 2),
        "momentum_score": round(momentum_score, 2),
        "close_location_score": round(close_location, 2),
        "volatility_score": round(volatility_score, 2),
        "trend_score": round(trend_score, 2),
        "turnover_score": round(turnover_score, 2),
        "volume_ratio_score": round(volume_ratio_score, 2),
        "risk_penalty": round(risk_penalty, 2),
        "amplitude": round(amplitude, 2),
    }


def grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def lane(score: float) -> str:
    if score >= 78:
        return "优先研究"
    if score >= 68:
        return "观察池"
    if score >= 58:
        return "学习池"
    return "剔除备选"


def filter_and_score(df: pd.DataFrame, min_amount: float, min_price: float) -> tuple[pd.DataFrame, dict[str, int]]:
    counts = {"raw": len(df)}
    clean = df.copy()
    clean = clean[clean["code"].map(is_main_a_share)]
    counts["main_a_share"] = len(clean)
    clean = clean[~clean["name"].str.contains("ST|退|N|C", case=False, regex=True, na=False)]
    counts["non_st_old_stock"] = len(clean)
    clean = clean[pd.to_numeric(clean["price"], errors="coerce") >= min_price]
    counts["price_filter"] = len(clean)
    clean = clean[pd.to_numeric(clean["amount"], errors="coerce") >= min_amount]
    counts["amount_filter"] = len(clean)
    clean = clean[pd.to_numeric(clean["pct_chg"], errors="coerce").notna()]
    counts["valid_pct"] = len(clean)

    score_rows = clean.apply(metric_scores, axis=1, result_type="expand")
    clean = pd.concat([clean.reset_index(drop=True), score_rows.reset_index(drop=True)], axis=1)
    clean["grade"] = clean["score"].map(grade)
    clean["lane"] = clean["score"].map(lane)
    clean = clean.sort_values(["score", "amount"], ascending=[False, False]).reset_index(drop=True)
    clean.insert(0, "rank", clean.index + 1)
    return clean, counts


def read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def set_env_value(path: Path, key: str, value: str) -> None:
    lines = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def money_yi(value: Any) -> str:
    return f"{safe_float(value) / 100_000_000:.2f}亿"


def price(value: Any) -> str:
    return f"{safe_float(value):.2f}"


def pct(value: Any) -> str:
    return f"{safe_float(value):.2f}%"


def plan_for(row: pd.Series) -> dict[str, Any]:
    p = safe_float(row.get("price"))
    high = safe_float(row.get("high"), p)
    low = safe_float(row.get("low"), p)
    amount = safe_float(row.get("amount"))
    entry_low = p * 0.97
    entry_high = p * 0.995
    breakout = max(high * 1.005, p * 1.025)
    stop = min(low * 0.985, p * 0.92) if low > 0 else p * 0.92
    return {
        "code": row["code"],
        "name": row["name"],
        "rank": int(row["rank"]),
        "score": safe_float(row["score"]),
        "grade": row["grade"],
        "lane": row["lane"],
        "snapshot_price": round(p, 2),
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "breakout_trigger": round(breakout, 2),
        "risk_stop": round(stop, 2),
        "reference_amount_yi": round(amount / 100_000_000, 2),
        "invalid_conditions": [
            "次日跌破风控价或评分降到 60 以下",
            "放量上攻失败后收回触发位下方",
            "成交额较当前基准萎缩超过 40%",
        ],
    }


def markdown_table(rows: pd.DataFrame, limit: int = 20) -> str:
    headers = ["排名", "代码", "名称", "评分", "层级", "现价", "涨跌幅", "成交额", "振幅"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in rows.head(limit).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(int(row["rank"])),
                    row["code"],
                    row["name"],
                    f"{safe_float(row['score']):.2f}",
                    row["lane"],
                    price(row["price"]),
                    pct(row["pct_chg"]),
                    money_yi(row["amount"]),
                    pct(row["amplitude"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_learning_report(
    report_path: Path,
    pool: pd.DataFrame,
    selected: pd.DataFrame,
    counts: dict[str, int],
    source: str,
    source_errors: list[str],
    stock_list: str,
) -> None:
    warnings = []
    if source == "stock_zh_a_spot":
        warnings.append("本次使用 AKShare 新浪全市场行情，稳定性较好，但缺少行业、PE/PB、换手率、量比等东财扩展字段。")
    if source_errors:
        warnings.append("备用源情况：" + "；".join(source_errors))
    body = [
        f"# A股 AI 学习池日报 - {now_cn():%Y-%m-%d}",
        "",
        f"- 数据源：AKShare `{source}`",
        f"- 原始数量：{counts.get('raw', 0)}，主板/创业板/科创板：{counts.get('main_a_share', 0)}，成交额过滤后：{counts.get('amount_filter', 0)}",
        f"- 已写入 daily_stock_analysis 的 `{len(selected)}` 只股票：`{stock_list}`",
        "",
        "## 当前进入分析队列",
        markdown_table(selected, limit=len(selected)),
        "",
        "## 学习池 Top 20",
        markdown_table(pool, limit=20),
        "",
        "## 评分口径",
        "评分主要看成交额流动性、当日动量、收盘位置、振幅、可得趋势字段，并对低价、极端涨跌和流动性不足做扣分。",
        "这是学习池/观察池筛选，不是自动买卖信号；后续还要交给 daily_stock_analysis 做基本面、消息面和 AI 解释。",
    ]
    if warnings:
        body += ["", "## 数据质量提醒"] + [f"- {item}" for item in warnings]
    report_path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")


def write_trade_plan(report_path: Path, plans: list[dict[str, Any]]) -> None:
    lines = [
        f"# A股 AI 交易计划层 - {now_cn():%Y-%m-%d}",
        "",
        "定位：只给个人研究和规则化观察使用，不做自动下单。",
        "",
    ]
    for item in plans:
        lines += [
            f"## {item['rank']}. {item['name']}({item['code']})",
            "",
            f"- 学习池评分：{item['score']:.2f} / {item['grade']}，层级：{item['lane']}",
            f"- 观察价：{item['snapshot_price']:.2f}",
            f"- 回踩观察区：{item['entry_low']:.2f} - {item['entry_high']:.2f}",
            f"- 放量突破触发：站上 {item['breakout_trigger']:.2f}，且成交额不低于约 {item['reference_amount_yi'] * 0.8:.2f} 亿",
            f"- 风控降级：跌破 {item['risk_stop']:.2f}",
            "- 降级条件：" + "；".join(item["invalid_conditions"]),
            "",
        ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def latest_previous_selected(output_dir: Path, today_key: str) -> Path | None:
    candidates = sorted(output_dir.glob("selected_*.json"))
    previous = [path for path in candidates if today_key not in path.name]
    return previous[-1] if previous else None


def write_review(report_path: Path, output_dir: Path, current: pd.DataFrame, today_key: str) -> None:
    prev_path = latest_previous_selected(output_dir, today_key)
    lines = [f"# A股 AI 复盘层 - {now_cn():%Y-%m-%d}", ""]
    if not prev_path:
        lines += ["暂无上一期交易计划，本次先建立基线。"]
        report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return

    previous = json.loads(prev_path.read_text(encoding="utf-8"))
    current_by_code = {str(row["code"]): row for _, row in current.iterrows()}
    lines += [f"对比计划：`{prev_path.name}`", ""]
    for item in previous:
        code = str(item["code"])
        row = current_by_code.get(code)
        if row is None:
            lines += [f"- {item['name']}({code})：今日未进入有效行情池，降级为资料复查。"]
            continue
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        current_price = safe_float(row.get("price"))
        trigger = safe_float(item.get("breakout_trigger"))
        stop = safe_float(item.get("risk_stop"))
        if high >= trigger:
            status = "触发突破观察"
        elif low <= stop:
            status = "触发风控降级"
        elif safe_float(item.get("entry_low")) <= current_price <= safe_float(item.get("entry_high")):
            status = "进入回踩观察区"
        else:
            status = "未触发，继续观察"
        lines += [
            f"- {item['name']}({code})：{status}；现价 {current_price:.2f}，涨跌幅 {pct(row.get('pct_chg'))}，当前评分 {safe_float(row.get('score')):.2f}。"
        ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    today = now_cn().strftime("%Y%m%d")

    raw, source, source_errors = load_spot_data()
    frame = normalized_frame(raw)
    scored, counts = filter_and_score(frame, args.min_amount, args.min_price)
    if scored.empty:
        raise RuntimeError("过滤后没有可用股票，请调低 min_amount/min_price 或检查行情源")

    pool = scored.head(args.pool_size).copy()
    selected = pool.head(args.analyze_count).copy()
    selected_codes = ",".join(selected["code"].astype(str).tolist())

    pool_csv = output_dir / f"learning_pool_{today}.csv"
    pool_json = output_dir / f"learning_pool_{today}.json"
    selected_json = output_dir / f"selected_{today}.json"
    current_csv = output_dir / "current_learning_pool.csv"
    current_json = output_dir / "current_learning_pool.json"
    current_stock_list = output_dir / "current_stock_list.txt"

    pool.to_csv(pool_csv, index=False, encoding="utf-8-sig")
    pool.to_json(pool_json, orient="records", force_ascii=False, indent=2)
    pool.to_csv(current_csv, index=False, encoding="utf-8-sig")
    pool.to_json(current_json, orient="records", force_ascii=False, indent=2)
    current_stock_list.write_text(selected_codes + "\n", encoding="utf-8")

    plans = [plan_for(row) for _, row in selected.iterrows()]
    selected_json.write_text(json.dumps(plans, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.dry_run:
        set_env_value(Path(args.env_file), args.stock_list_key, selected_codes)

    learning_report = report_dir / f"learning_pool_{today}.md"
    trade_plan_report = report_dir / f"trade_plan_{today}.md"
    review_report = report_dir / f"review_{today}.md"
    write_learning_report(learning_report, pool, selected, counts, source, source_errors, selected_codes)
    write_trade_plan(trade_plan_report, plans)
    write_review(review_report, output_dir, scored, today)

    summary = {
        "ok": True,
        "date": today,
        "source": source,
        "raw_count": counts.get("raw"),
        "pool_count": len(pool),
        "selected_count": len(selected),
        "stock_list": selected_codes,
        "reports": [str(learning_report), str(trade_plan_report), str(review_report)],
        "source_errors": source_errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
