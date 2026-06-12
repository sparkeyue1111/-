#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import signal
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import akshare as ak
import pandas as pd


LEADER_BASKET = {
    "600519": "贵州茅台",
    "300750": "宁德时代",
    "000333": "美的集团",
    "002594": "比亚迪",
    "600036": "招商银行",
    "601318": "中国平安",
    "600276": "恒瑞医药",
    "600900": "长江电力",
    "002415": "海康威视",
    "002475": "立讯精密",
    "600309": "万华化学",
    "601899": "紫金矿业",
    "000651": "格力电器",
    "300760": "迈瑞医疗",
    "601012": "隆基绿能",
    "000858": "五粮液",
    "601888": "中国中免",
    "002352": "顺丰控股",
    "688981": "中芯国际",
    "601919": "中远海控",
}

INDEX_SYMBOLS = {
    "沪深300": "sh000300",
    "中证500": "sh000905",
    "创业板指": "sz399006",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Historical proxy backtest for A-share AI research rules.")
    parser.add_argument("--learning-pool-dir", default="/app/data/fundamental_pool")
    parser.add_argument("--output-dir", default="/app/data/historical_backtest")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--years", type=int, default=int(os.environ.get("BACKTEST_YEARS", "5")))
    parser.add_argument("--max-codes", type=int, default=int(os.environ.get("BACKTEST_MAX_CODES", "50")))
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("BACKTEST_TOP_N", "5")))
    parser.add_argument("--random-trials", type=int, default=int(os.environ.get("BACKTEST_RANDOM_TRIALS", "30")))
    parser.add_argument("--thresholds", default=os.environ.get("BACKTEST_THRESHOLDS", "55,60,65,70,75,80"))
    parser.add_argument("--horizons", default=os.environ.get("BACKTEST_HORIZONS", "30,60,90"))
    parser.add_argument("--rebalance", choices=["M", "W"], default=os.environ.get("BACKTEST_REBALANCE", "M"))
    parser.add_argument("--min-amount", type=float, default=float(os.environ.get("BACKTEST_MIN_AMOUNT", "100000000")))
    parser.add_argument("--min-price", type=float, default=float(os.environ.get("BACKTEST_MIN_PRICE", "3")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "25")))
    parser.add_argument("--force-refresh", action="store_true")
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


def parse_int_list(text: str) -> list[int]:
    return [int(part.strip()) for part in text.split(",") if part.strip()]


def call_with_timeout(func, kwargs: dict[str, Any], timeout: int) -> tuple[pd.DataFrame, str]:
    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"timeout after {timeout}s")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)
    try:
        data = func(**kwargs)
        if isinstance(data, pd.DataFrame):
            return data, ""
        return pd.DataFrame(), "not dataframe"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:240]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def load_universe(learning_pool_dir: Path, max_codes: int) -> pd.DataFrame:
    path = learning_pool_dir / "current_learning_pool.csv"
    if not path.exists():
        raise SystemExit(f"missing current_learning_pool.csv: {path}")
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].map(lambda value: normalize_code(value).zfill(6))
    if "rank" in df.columns:
        df = df.sort_values("rank")
    elif "score" in df.columns:
        df = df.sort_values("score", ascending=False)
    df = df.drop_duplicates("code").head(max_codes)
    return df[["code", "name"]].copy()


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    columns = {
        "date": "日期" if "日期" in df.columns else "date",
        "code": "股票代码" if "股票代码" in df.columns else ("code" if "code" in df.columns else ""),
        "open": "开盘" if "开盘" in df.columns else "open",
        "close": "收盘" if "收盘" in df.columns else "close",
        "high": "最高" if "最高" in df.columns else "high",
        "low": "最低" if "最低" in df.columns else "low",
        "volume": "成交量" if "成交量" in df.columns else "volume",
        "amount": "成交额" if "成交额" in df.columns else "amount",
        "pct_chg": "涨跌幅" if "涨跌幅" in df.columns else "pct_chg",
        "turnover": "换手率" if "换手率" in df.columns else "turnover",
    }
    if columns["date"] not in df.columns or columns["close"] not in df.columns:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[columns["date"]], errors="coerce")
    for field, source in columns.items():
        if field == "date":
            continue
        if source and source in df.columns:
            if field == "code":
                out[field] = df[source].map(lambda value: normalize_code(value).zfill(6))
            else:
                out[field] = pd.to_numeric(df[source].astype(str).str.replace(",", "", regex=False), errors="coerce")
        else:
            out[field] = math.nan
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    if "pct_chg" in out.columns and out["pct_chg"].isna().all():
        out["pct_chg"] = out["close"].pct_change() * 100
    amount_median = pd.to_numeric(out["amount"], errors="coerce").dropna().median() if "amount" in out.columns else math.nan
    close_median = pd.to_numeric(out["close"], errors="coerce").dropna().median() if "close" in out.columns else math.nan
    if not math.isnan(amount_median) and not math.isnan(close_median) and amount_median < 10_000_000:
        # Tencent's daily endpoint labels share/lot volume as amount. Estimate traded value for liquidity filters.
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce") * pd.to_numeric(out["close"], errors="coerce") * 100
    return out


def market_symbol(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


def fetch_stock_history(code: str, start_date: str, end_date: str, cache_dir: Path, args: argparse.Namespace) -> tuple[pd.DataFrame, str]:
    cache_path = cache_dir / "stocks" / f"{code}_{start_date}_{end_date}_qfq.csv"
    if cache_path.exists() and not args.force_refresh:
        try:
            return normalize_hist(pd.read_csv(cache_path, dtype={"股票代码": str})), ""
        except Exception:
            pass
    attempts = [
        (
            "stock_zh_a_hist_em",
            ak.stock_zh_a_hist,
            {"symbol": code, "period": "daily", "start_date": start_date, "end_date": end_date, "adjust": "qfq", "timeout": args.timeout},
        ),
        (
            "stock_zh_a_daily_sina",
            ak.stock_zh_a_daily,
            {"symbol": market_symbol(code), "start_date": start_date, "end_date": end_date, "adjust": "qfq"},
        ),
        (
            "stock_zh_a_hist_tx",
            ak.stock_zh_a_hist_tx,
            {"symbol": market_symbol(code), "start_date": start_date, "end_date": end_date, "adjust": "qfq", "timeout": args.timeout},
        ),
    ]
    errors = []
    for source_name, func, kwargs in attempts:
        raw, error = call_with_timeout(func, kwargs, args.timeout + 5)
        if error or raw.empty:
            errors.append(f"{source_name}: {error or 'empty'}")
            continue
        hist = normalize_hist(raw)
        if hist.empty:
            errors.append(f"{source_name}: normalized empty")
            continue
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(cache_path, index=False, encoding="utf-8-sig")
        return hist, ""
    return pd.DataFrame(), "; ".join(errors)


def normalize_index_hist(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    date_col = "date" if "date" in df.columns else ("日期" if "日期" in df.columns else None)
    close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if date_col is None or close_col is None:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col], errors="coerce")
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    out["symbol"] = symbol
    return out.dropna(subset=["date", "close"]).sort_values("date")


def fetch_index_history(name: str, symbol: str, start_date: str, end_date: str, cache_dir: Path, args: argparse.Namespace) -> tuple[pd.DataFrame, str]:
    cache_path = cache_dir / "indexes" / f"{symbol}_{start_date}_{end_date}.csv"
    if cache_path.exists() and not args.force_refresh:
        try:
            return normalize_index_hist(pd.read_csv(cache_path), symbol), ""
        except Exception:
            pass
    raw, error = call_with_timeout(ak.stock_zh_index_daily, {"symbol": symbol}, args.timeout)
    if error or raw.empty:
        return pd.DataFrame(), f"{name}: {error or 'empty'}"
    raw = raw.copy()
    date_col = "date" if "date" in raw.columns else ("日期" if "日期" in raw.columns else None)
    if date_col:
        raw["_date"] = pd.to_datetime(raw[date_col], errors="coerce")
        raw = raw[(raw["_date"] >= pd.to_datetime(start_date)) & (raw["_date"] <= pd.to_datetime(end_date))]
        raw = raw.drop(columns=["_date"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return normalize_index_hist(raw, symbol), ""


def rebalance_dates(calendar: list[pd.Timestamp], mode: str) -> list[pd.Timestamp]:
    if not calendar:
        return []
    dates = pd.Series(sorted(calendar))
    frame = pd.DataFrame({"date": dates})
    if mode == "W":
        frame["period"] = frame["date"].dt.to_period("W")
    else:
        frame["period"] = frame["date"].dt.to_period("M")
    return frame.groupby("period")["date"].max().tolist()


def row_at_or_before(hist: pd.DataFrame, date: pd.Timestamp) -> tuple[int, pd.Series | None]:
    if hist.empty:
        return -1, None
    idx = hist["date"].searchsorted(date, side="right") - 1
    if idx < 0:
        return -1, None
    return int(idx), hist.iloc[int(idx)]


def row_at_or_after(hist: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    if hist.empty:
        return None
    idx = hist["date"].searchsorted(date, side="left")
    if idx >= len(hist):
        return None
    return hist.iloc[int(idx)]


def score_at(hist: pd.DataFrame, idx: int, min_amount: float, min_price: float) -> dict[str, Any] | None:
    if idx < 130:
        return None
    window20 = hist.iloc[idx - 19 : idx + 1]
    window60 = hist.iloc[idx - 59 : idx + 1]
    window120 = hist.iloc[idx - 119 : idx + 1]
    row = hist.iloc[idx]
    close = safe_float(row.get("close"))
    if math.isnan(close) or close < min_price:
        return None
    avg_amount_20 = safe_float(window20["amount"].mean())
    if math.isnan(avg_amount_20) or avg_amount_20 < min_amount:
        return None
    close20 = safe_float(hist.iloc[idx - 20].get("close"))
    close60 = safe_float(hist.iloc[idx - 60].get("close"))
    close120 = safe_float(hist.iloc[idx - 120].get("close"))
    ret20 = close / close20 - 1 if close20 > 0 else math.nan
    ret60 = close / close60 - 1 if close60 > 0 else math.nan
    ret120 = close / close120 - 1 if close120 > 0 else math.nan
    pct = pd.to_numeric(window60["pct_chg"], errors="coerce").dropna() / 100
    vol60 = float(pct.std() * math.sqrt(252) * 100) if len(pct) > 10 else math.nan
    high120 = safe_float(window120["close"].max())
    drawdown120 = close / high120 - 1 if high120 > 0 else math.nan

    liquidity_score = clamp(25 + 28 * math.log10(max(avg_amount_20, 1) / 100_000_000))
    if avg_amount_20 >= 800_000_000:
        liquidity_score = max(liquidity_score, 78)
    if avg_amount_20 >= 2_000_000_000:
        liquidity_score = max(liquidity_score, 88)
    trend_score = 50.0
    if not math.isnan(ret60):
        trend_score += clamp(ret60 * 100, -40, 80) * 0.55
    if not math.isnan(ret120):
        trend_score += clamp(ret120 * 100, -50, 120) * 0.35
    trend_score = clamp(trend_score)
    heat_score = 75.0
    if not math.isnan(ret20):
        if ret20 > 0.35:
            heat_score -= 30
        elif ret20 > 0.20:
            heat_score -= 15
        elif ret20 < -0.25:
            heat_score -= 12
    if not math.isnan(ret60) and ret60 > 0.90:
        heat_score -= 18
    heat_score = clamp(heat_score)
    risk_score = 70.0
    if not math.isnan(vol60):
        risk_score -= max(0, vol60 - 35) * 0.45
    if not math.isnan(drawdown120):
        risk_score += clamp((drawdown120 + 0.20) * 80, -18, 12)
    risk_score = clamp(risk_score)
    proxy_score = clamp(liquidity_score * 0.25 + trend_score * 0.40 + heat_score * 0.20 + risk_score * 0.15)
    return {
        "close": close,
        "avg_amount_20": avg_amount_20,
        "ret20": ret20 * 100,
        "ret60": ret60 * 100,
        "ret120": ret120 * 100,
        "vol60": vol60,
        "drawdown120": drawdown120 * 100,
        "liquidity_score": liquidity_score,
        "trend_score": trend_score,
        "heat_score": heat_score,
        "risk_score": risk_score,
        "proxy_score": proxy_score,
    }


def forward_return(hist: pd.DataFrame, start_date: pd.Timestamp, start_price: float, horizon: int) -> float:
    target_date = start_date + pd.Timedelta(days=horizon)
    row = row_at_or_after(hist, target_date)
    if row is None:
        return math.nan
    end_price = safe_float(row.get("close"))
    if math.isnan(start_price) or start_price <= 0 or math.isnan(end_price) or end_price <= 0:
        return math.nan
    return (end_price / start_price - 1) * 100


def index_forward_return(index_hist: pd.DataFrame, start_date: pd.Timestamp, horizon: int) -> float:
    start = row_at_or_after(index_hist, start_date)
    end = row_at_or_after(index_hist, start_date + pd.Timedelta(days=horizon))
    if start is None or end is None:
        return math.nan
    start_close = safe_float(start.get("close"))
    end_close = safe_float(end.get("close"))
    if start_close <= 0 or end_close <= 0 or math.isnan(start_close) or math.isnan(end_close):
        return math.nan
    return (end_close / start_close - 1) * 100


def equal_weight_return(items: list[dict[str, Any]], histories: dict[str, pd.DataFrame], date: pd.Timestamp, horizon: int) -> tuple[float, int]:
    returns = []
    for item in items:
        code = item["code"]
        ret = forward_return(histories.get(code, pd.DataFrame()), date, safe_float(item.get("close")), horizon)
        if not math.isnan(ret):
            returns.append(ret)
    if not returns:
        return math.nan, 0
    return sum(returns) / len(returns), len(returns)


def summarize(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    output = []
    for (group, horizon), part in df.groupby(["group", "horizon_days"]):
        returns = pd.to_numeric(part["portfolio_return"], errors="coerce").dropna()
        excess = pd.to_numeric(part["excess_vs_csi300"], errors="coerce").dropna()
        if returns.empty:
            continue
        output.append(
            {
                "group": group,
                "horizon_days": horizon,
                "samples": len(returns),
                "avg_return": round(float(returns.mean()), 4),
                "median_return": round(float(returns.median()), 4),
                "win_rate": round(float((returns > 0).mean() * 100), 2),
                "avg_excess_vs_csi300": round(float(excess.mean()), 4) if not excess.empty else "",
                "positive_excess_rate": round(float((excess > 0).mean() * 100), 2) if not excess.empty else "",
            }
        )
    return pd.DataFrame(output).sort_values(["horizon_days", "group"])


def turnover_row(group_name: str, current_codes: set[str], previous_codes: set[str]) -> dict[str, Any]:
    if not previous_codes:
        turnover = math.nan
        overlap = 0
    else:
        overlap = len(current_codes & previous_codes)
        turnover = 1 - overlap / max(len(current_codes), len(previous_codes), 1)
    return {
        "group": group_name,
        "current_count": len(current_codes),
        "previous_count": len(previous_codes),
        "overlap": overlap,
        "turnover_rate": round(turnover, 4) if not math.isnan(turnover) else "",
    }


def run_backtest(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], dict[str, Any]]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    universe = load_universe(Path(args.learning_pool_dir), args.max_codes)
    end_dt = datetime.strptime(args.date, "%Y%m%d")
    start_dt = end_dt.replace(year=end_dt.year - args.years)
    start_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    histories: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    names = {str(row["code"]).zfill(6): safe_text(row["name"]) for _, row in universe.iterrows()}
    all_codes = list(names.keys())
    for code in all_codes:
        print(f"[history] {code} {names.get(code, '')}", flush=True)
        hist, error = fetch_stock_history(code, start_date, end_date, cache_dir, args)
        if error:
            errors.append(f"{code}: {error}")
            continue
        if len(hist) < 180:
            errors.append(f"{code}: history too short ({len(hist)})")
            continue
        histories[code] = hist
        time.sleep(0.12)

    leader_histories: dict[str, pd.DataFrame] = {}
    for code, name in LEADER_BASKET.items():
        if code in histories:
            leader_histories[code] = histories[code]
            continue
        hist, error = fetch_stock_history(code, start_date, end_date, cache_dir, args)
        if not error and not hist.empty:
            leader_histories[code] = hist
        time.sleep(0.08)

    indexes = {}
    for name, symbol in INDEX_SYMBOLS.items():
        hist, error = fetch_index_history(name, symbol, start_date, end_date, cache_dir, args)
        if error:
            errors.append(error)
        if not hist.empty:
            indexes[name] = hist

    if not histories:
        raise SystemExit("no stock histories loaded")

    calendar = sorted(set().union(*[set(hist["date"].tolist()) for hist in histories.values()]))
    rebalances = rebalance_dates(calendar, args.rebalance)
    horizons = parse_int_list(args.horizons)
    thresholds = parse_int_list(args.thresholds)
    random.seed(int(args.date))

    trade_rows = []
    turnover_rows = []
    previous_top5: set[str] = set()
    previous_eligible: set[str] = set()

    for rebalance_date in rebalances:
        scored = []
        for code, hist in histories.items():
            idx, row = row_at_or_before(hist, rebalance_date)
            if row is None:
                continue
            item = score_at(hist, idx, args.min_amount, args.min_price)
            if item is None:
                continue
            scored.append({"code": code, "name": names.get(code, code), "date": rebalance_date, **item})
        if len(scored) < max(args.top_n, 3):
            continue
        scored = sorted(scored, key=lambda item: item["proxy_score"], reverse=True)
        top5 = scored[: args.top_n]
        eligible = [item for item in scored if item["proxy_score"] >= min(thresholds)]
        top5_codes = {item["code"] for item in top5}
        eligible_codes = {item["code"] for item in eligible}
        turnover_rows.append({"date": rebalance_date.strftime("%Y-%m-%d"), **turnover_row("proxy_top5", top5_codes, previous_top5)})
        turnover_rows.append({"date": rebalance_date.strftime("%Y-%m-%d"), **turnover_row("eligible_min_threshold", eligible_codes, previous_eligible)})
        previous_top5 = top5_codes
        previous_eligible = eligible_codes

        group_items: dict[str, list[dict[str, Any]]] = {"proxy_top5": top5}
        leader_items = []
        for code, hist in leader_histories.items():
            idx, row = row_at_or_before(hist, rebalance_date)
            if row is not None:
                leader_items.append({"code": code, "name": LEADER_BASKET.get(code, code), "close": safe_float(row.get("close"))})
        group_items["industry_leaders"] = leader_items
        for threshold in thresholds:
            group_items[f"threshold_{threshold}"] = [item for item in scored if item["proxy_score"] >= threshold][: args.top_n]

        liquid_pool = [item for item in scored if item["avg_amount_20"] >= args.min_amount and item["close"] >= args.min_price]
        random_returns_by_horizon: dict[int, list[float]] = {h: [] for h in horizons}
        for trial in range(args.random_trials):
            if len(liquid_pool) < args.top_n:
                sample = liquid_pool
            else:
                sample = random.sample(liquid_pool, args.top_n)
            for horizon in horizons:
                ret, n = equal_weight_return(sample, histories, rebalance_date, horizon)
                if not math.isnan(ret) and n > 0:
                    random_returns_by_horizon[horizon].append(ret)

        for horizon in horizons:
            csi300_return = index_forward_return(indexes.get("沪深300", pd.DataFrame()), rebalance_date, horizon)
            random_values = random_returns_by_horizon[horizon]
            if random_values:
                avg_random = sum(random_values) / len(random_values)
                trade_rows.append(
                    {
                        "date": rebalance_date.strftime("%Y-%m-%d"),
                        "group": "same_pool_random",
                        "horizon_days": horizon,
                        "member_count": args.top_n,
                        "portfolio_return": round(avg_random, 4),
                        "csi300_return": round(csi300_return, 4) if not math.isnan(csi300_return) else "",
                        "excess_vs_csi300": round(avg_random - csi300_return, 4) if not math.isnan(csi300_return) else "",
                    }
                )
            for group, items in group_items.items():
                if not items:
                    continue
                source_histories = leader_histories if group == "industry_leaders" else histories
                ret, n = equal_weight_return(items, source_histories, rebalance_date, horizon)
                if math.isnan(ret):
                    continue
                trade_rows.append(
                    {
                        "date": rebalance_date.strftime("%Y-%m-%d"),
                        "group": group,
                        "horizon_days": horizon,
                        "member_count": n,
                        "portfolio_return": round(ret, 4),
                        "csi300_return": round(csi300_return, 4) if not math.isnan(csi300_return) else "",
                        "excess_vs_csi300": round(ret - csi300_return, 4) if not math.isnan(csi300_return) else "",
                        "top_codes": ",".join(item["code"] for item in items[: args.top_n]),
                    }
                )

    trades = pd.DataFrame(trade_rows)
    summary = summarize(trade_rows)
    turnover = pd.DataFrame(turnover_rows)
    meta = {
        "run_date": args.date,
        "start_date": start_date,
        "end_date": end_date,
        "years": args.years,
        "universe_codes": len(all_codes),
        "loaded_histories": len(histories),
        "rebalance_count": len(rebalances),
        "rebalance": args.rebalance,
        "top_n": args.top_n,
        "random_trials": args.random_trials,
        "thresholds": thresholds,
        "horizons": horizons,
        "method_note": "候选池代理规则回测，不是历史 AI 回测，也不是全市场无偏回测。",
    }
    return trades, summary, turnover, errors, meta


def write_report(path: Path, summary: pd.DataFrame, turnover: pd.DataFrame, errors: list[str], meta: dict[str, Any]) -> None:
    lines = [
        f"# A股历史代理回测 V1 - {datetime.strptime(meta['run_date'], '%Y%m%d'):%Y-%m-%d}",
        "",
        "定位：用历史行情对“可量化代理规则”做压力测试，检查选股规则、分数阈值和换手稳定性。它不是历史 AI 回测，因为过去没有当时的 LLM 研究报告；也不是全市场无偏回测，因为 V1 使用当前基本面池候选做压力测试。",
        "",
        "## 回测范围",
        f"- 时间：{meta['start_date']} 至 {meta['end_date']}，约 {meta['years']} 年。",
        f"- 候选池：当前基本面池前 {meta['universe_codes']} 只，成功加载历史 {meta['loaded_histories']} 只。",
        f"- 调仓：{'月度' if meta['rebalance'] == 'M' else '周度'}，样本调仓点 {meta['rebalance_count']} 个。",
        f"- 对照：同池随机、行业龙头篮子、沪深300。",
        "",
        "## 核心结论",
    ]
    if summary.empty:
        lines.append("- 样本不足或行情源不可用，未生成有效回测结果。")
    else:
        top = summary[(summary["group"] == "proxy_top5") & (summary["horizon_days"] == min(meta["horizons"]))]
        random_row = summary[(summary["group"] == "same_pool_random") & (summary["horizon_days"] == min(meta["horizons"]))]
        if not top.empty and not random_row.empty:
            proxy_excess = safe_float(top.iloc[0].get("avg_return")) - safe_float(random_row.iloc[0].get("avg_return"))
            lines.append(f"- 最短持有周期下，代理 Top5 相对同池随机平均收益差：{proxy_excess:.2f} 个百分点。")
        lines.append("- 阈值是否有效要看 threshold_70/75/80 是否在 30/60/90 天都稳定优于随机和沪深300，不能只看单一周期。")
        lines.append("- 如果 proxy_top5 换手长期过高，说明每天新增名单不能直接交易，必须继续使用稳定性门槛。")

    lines += [
        "",
        "## 收益对照",
        "| 组别 | 持有天数 | 样本 | 平均收益 | 中位收益 | 胜率 | 平均超额沪深300 | 超额为正比例 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not summary.empty:
        for row in summary.itertuples(index=False):
            lines.append(
                f"| {row.group} | {row.horizon_days} | {row.samples} | {row.avg_return} | {row.median_return} | {row.win_rate} | {row.avg_excess_vs_csi300} | {row.positive_excess_rate} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines += [
        "",
        "## 换手稳定性",
        "| 组别 | 平均换手率 | 中位换手率 | 样本 |",
        "|---|---:|---:|---:|",
    ]
    if not turnover.empty:
        for group, part in turnover.groupby("group"):
            values = pd.to_numeric(part["turnover_rate"], errors="coerce").dropna()
            if values.empty:
                continue
            lines.append(f"| {group} | {values.mean():.2%} | {values.median():.2%} | {len(values)} |")
    else:
        lines.append("| - | - | - | - |")

    lines += [
        "",
        "## 使用限制",
        "- V1 使用当前基本面池候选，存在幸存者偏差和当前信息偏差。",
        "- V1 的历史分数是价格、流动性、不过热、波动和回撤构成的代理分，不等于 daily_stock_analysis 的真实 AI 分。",
        "- 这份报告只能决定下一步是否值得做全市场无偏回测，不能单独证明实盘策略有效。",
    ]
    if errors:
        lines += ["", "## 数据源提醒"]
        for error in errors[:40]:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    trades, summary, turnover, errors, meta = run_backtest(args)
    date_key = args.date
    trades.to_csv(output_dir / f"historical_backtest_trades_{date_key}.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output_dir / f"historical_backtest_summary_{date_key}.csv", index=False, encoding="utf-8-sig")
    turnover.to_csv(output_dir / f"historical_backtest_turnover_{date_key}.csv", index=False, encoding="utf-8-sig")
    (output_dir / f"historical_backtest_meta_{date_key}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = report_dir / f"historical_backtest_{date_key}.md"
    write_report(report_path, summary, turnover, errors, meta)
    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "trades": len(trades),
                "summary_rows": len(summary),
                "turnover_rows": len(turnover),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
