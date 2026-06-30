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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a fundamental-first A-share pool for 30-90 day holding plans.")
    parser.add_argument("--env-file", default="/app/data/app.env")
    parser.add_argument("--output-dir", default="/app/data/fundamental_pool")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--pool-size", type=int, default=int(os.environ.get("POOL_SIZE", "50")))
    parser.add_argument("--analyze-count", type=int, default=int(os.environ.get("ANALYZE_COUNT", "15")))
    parser.add_argument("--probe-count", type=int, default=int(os.environ.get("FUNDAMENTAL_PROBE_COUNT", "120")))
    parser.add_argument("--min-amount", type=float, default=float(os.environ.get("MIN_AMOUNT", "100000000")))
    parser.add_argument("--min-price", type=float, default=float(os.environ.get("MIN_PRICE", "3")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "25")))
    parser.add_argument("--stock-list-key", default="STOCK_LIST")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_cn() -> datetime:
    return datetime.now()


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


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def normalize_code(raw_code: Any) -> str:
    text = safe_text(raw_code).lower()
    match = re.search(r"(\d{6})$", text)
    if match:
        return match.group(1)
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def is_main_a_share(code: str) -> bool:
    return bool(re.fullmatch(r"[036]\d{5}", code))


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


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


def normalized_frame(raw: pd.DataFrame) -> pd.DataFrame:
    col = {
        "raw_code": pick_column(raw, ["代码", "code", "股票代码"]),
        "name": pick_column(raw, ["名称", "name", "股票名称"]),
        "price": pick_column(raw, ["最新价", "最新", "现价", "trade", "close"]),
        "pct_chg": pick_column(raw, ["涨跌幅", "涨幅", "changepercent", "pct_chg"]),
        "open": pick_column(raw, ["今开", "开盘", "open"]),
        "high": pick_column(raw, ["最高", "最高价", "high"]),
        "low": pick_column(raw, ["最低", "最低价", "low"]),
        "prev_close": pick_column(raw, ["昨收", "昨收价", "preclose", "settlement"]),
        "volume": pick_column(raw, ["成交量", "volume"]),
        "amount": pick_column(raw, ["成交额", "amount"]),
        "turnover_rate": pick_column(raw, ["换手率", "turnover"]),
        "volume_ratio": pick_column(raw, ["量比"]),
        "total_mv": pick_column(raw, ["总市值", "总市值-元", "总市值(元)"]),
        "circ_mv": pick_column(raw, ["流通市值", "流通市值-元", "流通市值(元)"]),
        "pct_60d": pick_column(raw, ["60日涨跌幅"]),
        "pct_ytd": pick_column(raw, ["年初至今涨跌幅"]),
    }
    required = ["raw_code", "name", "price", "pct_chg", "amount"]
    missing = [key for key in required if col[key] is None]
    if missing:
        raise RuntimeError(f"行情数据缺少必要字段: {missing}; columns={list(raw.columns)}")

    out = pd.DataFrame()
    out["code"] = raw[col["raw_code"]].map(normalize_code)
    out["name"] = raw[col["name"]].astype(str).str.strip()
    for field in [
        "price",
        "pct_chg",
        "open",
        "high",
        "low",
        "prev_close",
        "volume",
        "amount",
        "turnover_rate",
        "volume_ratio",
        "total_mv",
        "circ_mv",
        "pct_60d",
        "pct_ytd",
    ]:
        source = col.get(field)
        if source is None:
            out[field] = math.nan
        else:
            out[field] = pd.to_numeric(
                raw[source].astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
                errors="coerce",
            )
    return out


def liquidity_score(amount: float) -> float:
    if amount <= 0:
        return 0
    score = 25 + 28 * math.log10(max(amount, 1) / 100_000_000)
    if amount >= 800_000_000:
        score = max(score, 78)
    if amount >= 2_000_000_000:
        score = max(score, 88)
    return clamp(score)


def heat_score(pct_chg: float, amplitude: float) -> float:
    score = 75.0
    if pct_chg > 7:
        score -= 25
    elif pct_chg > 4:
        score -= 10
    elif pct_chg < -7:
        score -= 25
    elif pct_chg < -4:
        score -= 8
    if amplitude > 12:
        score -= 18
    elif amplitude > 8:
        score -= 8
    return clamp(score)


def trend_seed_score(row: pd.Series) -> float:
    pct_60d = safe_float(row.get("pct_60d"), math.nan)
    pct_ytd = safe_float(row.get("pct_ytd"), math.nan)
    values = []
    if not math.isnan(pct_60d):
        values.append(clamp(50 + pct_60d * 0.7))
    if not math.isnan(pct_ytd):
        values.append(clamp(50 + pct_ytd * 0.45))
    if values:
        return sum(values) / len(values)
    return 50.0


def preselect_score(row: pd.Series) -> dict[str, float]:
    price = safe_float(row.get("price"))
    high = safe_float(row.get("high"), price)
    low = safe_float(row.get("low"), price)
    prev_close = safe_float(row.get("prev_close"), price)
    amplitude = abs(high - low) / prev_close * 100 if prev_close > 0 else 0
    liq = liquidity_score(safe_float(row.get("amount")))
    heat = heat_score(safe_float(row.get("pct_chg")), amplitude)
    trend = trend_seed_score(row)
    return {
        "preselect_score": round(clamp(liq * 0.55 + heat * 0.30 + trend * 0.15), 2),
        "liquidity_score": round(liq, 2),
        "heat_score": round(heat, 2),
        "trend_seed_score": round(trend, 2),
        "amplitude": round(amplitude, 2),
    }


def filter_universe(df: pd.DataFrame, min_amount: float, min_price: float) -> tuple[pd.DataFrame, dict[str, int]]:
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

    pre = clean.apply(preselect_score, axis=1, result_type="expand")
    clean = pd.concat([clean.reset_index(drop=True), pre.reset_index(drop=True)], axis=1)
    clean = clean.sort_values(["preselect_score", "amount"], ascending=[False, False]).reset_index(drop=True)
    return clean, counts


def timeout_call(fn_name: str, kwargs: dict[str, Any], timeout: int) -> tuple[pd.DataFrame, str]:
    def handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{fn_name} timeout after {timeout}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(timeout)
    try:
        data = getattr(ak, fn_name)(**kwargs)
        if isinstance(data, pd.DataFrame):
            return data, ""
        return pd.DataFrame(), ""
    except TimeoutError:
        return pd.DataFrame(), f"{fn_name} timeout after {timeout}s"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:260]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def metric_value(latest: pd.DataFrame, patterns: list[str], columns: list[str]) -> float:
    if latest.empty or "metric_name" not in latest.columns:
        return math.nan
    pattern = re.compile("|".join(patterns), re.IGNORECASE)
    subset = latest[latest["metric_name"].astype(str).str.contains(pattern, na=False, regex=True)]
    if subset.empty:
        return math.nan
    for column in columns:
        if column in subset.columns:
            for value in subset[column].tolist():
                number = safe_float(value, math.nan)
                if not math.isnan(number):
                    return number
    return math.nan


def score_financial(metrics: dict[str, float]) -> tuple[float, list[str], list[str]]:
    score = 50.0
    positives: list[str] = []
    warnings: list[str] = []

    revenue_yoy = metrics["revenue_yoy"]
    if not math.isnan(revenue_yoy):
        if revenue_yoy >= 20:
            score += 14
            positives.append("营收同比增长较强")
        elif revenue_yoy > 0:
            score += 6
            positives.append("营收同比为正")
        elif revenue_yoy <= -20:
            score -= 22
            warnings.append("营收同比明显下滑")
        else:
            score -= 10
            warnings.append("营收同比下滑")

    profit_yoy = metrics["profit_yoy"]
    if not math.isnan(profit_yoy):
        if profit_yoy >= 30:
            score += 20
            positives.append("利润同比弹性较强")
        elif profit_yoy > 0:
            score += 9
            positives.append("利润同比为正")
        elif profit_yoy <= -30:
            score -= 28
            warnings.append("利润同比大幅下滑")
        else:
            score -= 16
            warnings.append("利润同比下滑")

    ocf = metrics["operating_cash_flow"]
    if not math.isnan(ocf):
        if ocf > 0:
            score += 14
            positives.append("经营现金流为正")
        else:
            score -= 18
            warnings.append("经营现金流为负")

    roe = metrics["roe"]
    if not math.isnan(roe):
        if roe >= 8:
            score += 8
            positives.append("ROE 较好")
        elif roe < 3:
            score -= 8
            warnings.append("ROE 偏低")

    gross_margin = metrics["gross_margin"]
    if not math.isnan(gross_margin):
        if gross_margin >= 25:
            score += 5
        elif gross_margin < 10:
            score -= 6
            warnings.append("毛利率偏低")

    debt_ratio = metrics["debt_ratio"]
    if not math.isnan(debt_ratio):
        if debt_ratio <= 55:
            score += 4
        elif debt_ratio >= 75:
            score -= 10
            warnings.append("资产负债率偏高")

    available = sum(0 if math.isnan(value) else 1 for value in metrics.values())
    if available < 3:
        score -= 12
        warnings.append("关键财务指标缺失较多")
    return clamp(score), positives, warnings


def collect_financial(code: str, timeout: int) -> tuple[dict[str, Any], str]:
    raw, error = timeout_call("stock_financial_abstract_new_ths", {"symbol": code}, timeout)
    if raw.empty:
        return {
            "report_date": "",
            "fundamental_score": 30.0,
            "available_metric_count": 0,
            "fundamental_notes": "财务摘要接口无数据",
            "fundamental_warnings": "缺少财务摘要，不能进入强持仓池",
            "metric_revenue_yoy": math.nan,
            "metric_profit_yoy": math.nan,
            "metric_operating_cash_flow": math.nan,
            "metric_roe": math.nan,
            "metric_gross_margin": math.nan,
            "metric_debt_ratio": math.nan,
        }, error

    raw = raw.copy()
    raw["_report_date"] = pd.to_datetime(raw.get("report_date"), errors="coerce")
    latest_date = raw["_report_date"].dropna().max()
    latest = raw[raw["_report_date"] == latest_date] if not pd.isna(latest_date) else raw.head(120)
    metrics = {
        "revenue_yoy": metric_value(latest, [r"operat.*income.*yoy", r"revenue.*yoy", r"营业.*收入.*同比", r"income_yoy"], ["value", "yoy", "single_yoy"]),
        "profit_yoy": metric_value(latest, [r"deduct_net_profit_yoy", r"parent.*net.*profit.*yoy", r"net_profit.*yoy", r"归母.*同比", r"扣非.*同比"], ["value", "yoy", "single_yoy"]),
        "operating_cash_flow": metric_value(latest, [r"operating_cash_flow", r"per_operating_cash_flow_net", r"经营.*现金"], ["value", "single"]),
        "roe": metric_value(latest, [r"\broe\b", r"net_asset_yield", r"净资产收益率"], ["value", "single"]),
        "gross_margin": metric_value(latest, [r"gross_profit", r"毛利率"], ["value", "single"]),
        "debt_ratio": metric_value(latest, [r"asset_liability", r"资产负债率"], ["value", "single"]),
    }
    score, positives, warnings = score_financial(metrics)
    available = sum(0 if math.isnan(value) else 1 for value in metrics.values())
    notes = positives + [
        f"营收同比={metrics['revenue_yoy']:.2f}%" if not math.isnan(metrics["revenue_yoy"]) else "营收同比=缺失",
        f"利润同比={metrics['profit_yoy']:.2f}%" if not math.isnan(metrics["profit_yoy"]) else "利润同比=缺失",
        f"经营现金流={metrics['operating_cash_flow']:.2f}" if not math.isnan(metrics["operating_cash_flow"]) else "经营现金流=缺失",
    ]
    return {
        "report_date": latest_date.strftime("%Y-%m-%d") if not pd.isna(latest_date) else "",
        "fundamental_score": round(score, 2),
        "available_metric_count": available,
        "fundamental_notes": "；".join(notes),
        "fundamental_warnings": "；".join(warnings) if warnings else "暂无硬性财务红旗",
        "metric_revenue_yoy": metrics["revenue_yoy"],
        "metric_profit_yoy": metrics["profit_yoy"],
        "metric_operating_cash_flow": metrics["operating_cash_flow"],
        "metric_roe": metrics["roe"],
        "metric_gross_margin": metrics["gross_margin"],
        "metric_debt_ratio": metrics["debt_ratio"],
    }, error


def grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def lane(score: float) -> str:
    if score >= 72:
        return "基本面优先"
    if score >= 65:
        return "基本面观察"
    if score >= 58:
        return "资料复核"
    return "剔除备选"


def build_pool(candidates: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    probe = candidates.head(args.probe_count).copy()
    total = len(probe)
    for idx, (_, row) in enumerate(probe.iterrows(), start=1):
        code = str(row["code"]).zfill(6)
        name = safe_text(row["name"])
        print(f"[fundamental] {idx}/{total} {code} {name}", flush=True)
        financial, error = collect_financial(code, args.timeout)
        if error:
            errors.append(f"{code} {error}")
        midterm_score = (
            safe_float(financial.get("fundamental_score")) * 0.70
            + safe_float(row.get("liquidity_score")) * 0.12
            + safe_float(row.get("heat_score")) * 0.10
            + safe_float(row.get("trend_seed_score")) * 0.08
        )
        out = row.to_dict()
        out.update(financial)
        out["score"] = round(clamp(midterm_score), 2)
        out["grade"] = grade(out["score"])
        out["lane"] = lane(out["score"])
        out["pool_type"] = "fundamental_first_midterm"
        rows.append(out)
        time.sleep(0.15)
    pool = pd.DataFrame(rows)
    if pool.empty:
        raise RuntimeError("fundamental pool is empty")
    pool = pool.sort_values(["score", "fundamental_score", "amount"], ascending=[False, False, False]).reset_index(drop=True)
    pool.insert(0, "rank", pool.index + 1)
    return pool, errors


def read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    out = []
    found = False
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


def pct(value: Any) -> str:
    number = safe_float(value, math.nan)
    return "缺失" if math.isnan(number) else f"{number:.2f}%"


def price(value: Any) -> str:
    return f"{safe_float(value):.2f}"


def midterm_plan_for(row: pd.Series) -> dict[str, Any]:
    current = safe_float(row.get("price"))
    low = safe_float(row.get("low"), current)
    entry_low = current * 0.94
    entry_high = current * 0.985
    breakout = current * 1.035
    risk_stop = min(low * 0.97, current * 0.88) if low > 0 else current * 0.88
    return {
        "code": str(row["code"]).zfill(6),
        "name": safe_text(row["name"]),
        "rank": int(row["rank"]),
        "score": safe_float(row["score"]),
        "grade": safe_text(row["grade"]),
        "lane": safe_text(row["lane"]),
        "snapshot_price": round(current, 2),
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "breakout_trigger": round(breakout, 2),
        "risk_stop": round(risk_stop, 2),
        "review_days": [30, 60, 90],
        "holding_logic": "基本面池候选，等待 AI 趋势和证据层确认后才进入持仓计划。",
        "invalid_conditions": [
            "下一次 final_score 低于 60",
            "AI 趋势转看空且无法在下一期修复",
            "经营现金流继续恶化或利润增长被证伪",
            "公告/问询函出现收入确认、关联交易、资金占用等硬伤",
        ],
    }


def markdown_table(rows: pd.DataFrame, limit: int = 20) -> str:
    headers = ["排名", "代码", "名称", "总分", "基本面分", "层级", "财报期", "营收同比", "利润同比", "经营现金流", "成交额"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in rows.head(limit).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(int(row["rank"])),
                    str(row["code"]).zfill(6),
                    safe_text(row["name"]),
                    f"{safe_float(row['score']):.2f}",
                    f"{safe_float(row['fundamental_score']):.2f}",
                    safe_text(row["lane"]),
                    safe_text(row.get("report_date")),
                    pct(row.get("metric_revenue_yoy")),
                    pct(row.get("metric_profit_yoy")),
                    f"{safe_float(row.get('metric_operating_cash_flow'), math.nan):.2f}" if not math.isnan(safe_float(row.get("metric_operating_cash_flow"), math.nan)) else "缺失",
                    money_yi(row.get("amount")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_report(report_path: Path, pool: pd.DataFrame, selected: pd.DataFrame, counts: dict[str, int], source: str, stock_list: str, errors: list[str], probe_count: int) -> None:
    lines = [
        f"# A股 AI 基本面池日报 - {now_cn():%Y-%m-%d}",
        "",
        "定位：服务 30-90 天或更长持仓，不以短线涨跌幅为主。先看财务质量，再看流动性和中期可交易性。",
        "",
        f"- 行情源：AKShare `{source}`",
        f"- 原始数量：{counts.get('raw', 0)}；过滤后：{counts.get('amount_filter', 0)}；财务探测：{probe_count} 只",
        f"- 已写入 daily_stock_analysis 的股票：`{stock_list}`",
        "",
        "## 当前进入 AI 分析队列",
        markdown_table(selected, limit=len(selected)),
        "",
        "## 基本面池 Top 20",
        markdown_table(pool, limit=20),
        "",
        "## 评分口径",
        "- 基本面质量约占 70%：营收同比、利润同比、经营现金流、ROE、毛利率、资产负债率。",
        "- 可交易性约占 30%：成交额流动性、不过热程度、可得趋势字段。",
        "- 这是研究优先级，不是买入指令；后续还要经过 daily_stock_analysis、证据层和最终交易计划层。",
    ]
    if errors:
        lines += ["", "## 数据源提醒"] + [f"- {item}" for item in errors[:30]]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_midterm_plan(report_path: Path, plans: list[dict[str, Any]]) -> None:
    lines = [
        f"# A股 AI 中期持仓候选计划 - {now_cn():%Y-%m-%d}",
        "",
        "定位：这是基本面池的初始计划，只有后续 AI 趋势、证据质量和 final_score 同时确认，才升级为正式持仓计划。",
        "",
    ]
    for item in plans:
        lines += [
            f"## {item['rank']}. {item['name']}({item['code']})",
            f"- 基本面池评分：{item['score']:.2f} / {item['grade']}，层级：{item['lane']}",
            f"- 当前观察价：{item['snapshot_price']:.2f}",
            f"- 分批观察区：{item['entry_low']:.2f} - {item['entry_high']:.2f}",
            f"- 强势确认价：{item['breakout_trigger']:.2f}",
            f"- 中期风控价：{item['risk_stop']:.2f}",
            "- 30/60/90 天复核：财报、公告、订单/客户认证、经营现金流、AI 趋势评分。",
            "- 降级条件：" + "；".join(item["invalid_conditions"]),
            "",
        ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_key = now_cn().strftime("%Y%m%d")

    raw, source, source_errors = load_spot_data()
    frame = normalized_frame(raw)
    universe, counts = filter_universe(frame, args.min_amount, args.min_price)
    if universe.empty:
        raise RuntimeError("过滤后没有可用股票，请检查行情源或调低过滤条件")

    pool, financial_errors = build_pool(universe, args)
    pool = pool.head(args.pool_size).copy()
    core_count = min(5, args.analyze_count, len(pool))
    rotation_count = max(0, args.analyze_count - core_count)
    core = pool.head(core_count).copy()
    rest = pool.iloc[core_count:].copy()
    if rotation_count > 0 and not rest.empty:
        offset = (int(date_key[-2:]) * rotation_count) % len(rest)
        rotated = pd.concat([rest.iloc[offset:], rest.iloc[:offset]], ignore_index=True).head(rotation_count)
        selected = pd.concat([core, rotated], ignore_index=True)
    else:
        selected = core
    stock_list = ",".join(selected["code"].astype(str).str.zfill(6).tolist())

    pool_csv = output_dir / f"fundamental_pool_{date_key}.csv"
    pool_json = output_dir / f"fundamental_pool_{date_key}.json"
    selected_json = output_dir / f"selected_{date_key}.json"
    current_csv = output_dir / "current_learning_pool.csv"
    current_json = output_dir / "current_learning_pool.json"
    current_stock_list = output_dir / "current_stock_list.txt"
    latest_alias = output_dir / "current_fundamental_pool.csv"

    pool.to_csv(pool_csv, index=False, encoding="utf-8-sig")
    pool.to_json(pool_json, orient="records", force_ascii=False, indent=2)
    pool.to_csv(current_csv, index=False, encoding="utf-8-sig")
    pool.to_json(current_json, orient="records", force_ascii=False, indent=2)
    pool.to_csv(latest_alias, index=False, encoding="utf-8-sig")
    current_stock_list.write_text(stock_list + "\n", encoding="utf-8")

    plans = [midterm_plan_for(row) for _, row in selected.iterrows()]
    selected_json.write_text(json.dumps(plans, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.dry_run:
        set_env_value(Path(args.env_file), args.stock_list_key, stock_list)

    errors = source_errors + financial_errors
    fundamental_report = report_dir / f"fundamental_pool_{date_key}.md"
    midterm_plan_report = report_dir / f"midterm_holding_plan_{date_key}.md"
    write_report(fundamental_report, pool, selected, counts, source, stock_list, errors, min(args.probe_count, len(universe)))
    write_midterm_plan(midterm_plan_report, plans)

    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "source": source,
                "probe_count": min(args.probe_count, len(universe)),
                "pool_count": len(pool),
                "selected_count": len(selected),
                "stock_list": stock_list,
                "reports": [str(fundamental_report), str(midterm_plan_report)],
                "source_errors": errors[:20],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
