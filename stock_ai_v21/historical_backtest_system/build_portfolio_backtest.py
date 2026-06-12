#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
from dataclasses import dataclass, field
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
    parser = argparse.ArgumentParser(description="Capital-account portfolio backtest for A-share research rules.")
    parser.add_argument("--learning-pool-dir", default="/app/data/fundamental_pool")
    parser.add_argument("--output-dir", default="/app/data/historical_backtest")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--years", type=int, default=int(os.environ.get("BACKTEST_YEARS", "5")))
    parser.add_argument("--max-codes", type=int, default=int(os.environ.get("BACKTEST_MAX_CODES", "50")))
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("BACKTEST_TOP_N", "5")))
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("BACKTEST_PORTFOLIO_THRESHOLD", "80")))
    parser.add_argument("--rebalance", choices=["M", "W"], default=os.environ.get("BACKTEST_REBALANCE", "M"))
    parser.add_argument("--initial-capital", type=float, default=float(os.environ.get("BACKTEST_INITIAL_CAPITAL", "100000")))
    parser.add_argument("--commission-rate", type=float, default=float(os.environ.get("BACKTEST_COMMISSION_RATE", "0.0003")))
    parser.add_argument("--stamp-duty-rate", type=float, default=float(os.environ.get("BACKTEST_STAMP_DUTY_RATE", "0.0005")))
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
    out = out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if "pct_chg" in out.columns and out["pct_chg"].isna().all():
        out["pct_chg"] = out["close"].pct_change() * 100
    amount_median = pd.to_numeric(out["amount"], errors="coerce").dropna().median() if "amount" in out.columns else math.nan
    close_median = pd.to_numeric(out["close"], errors="coerce").dropna().median() if "close" in out.columns else math.nan
    if not math.isnan(amount_median) and not math.isnan(close_median) and amount_median < 10_000_000:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce") * pd.to_numeric(out["close"], errors="coerce") * 100
    return out


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
    return out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


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


def price_at(hist: pd.DataFrame, date: pd.Timestamp) -> float:
    _, row = row_at_or_before(hist, date)
    if row is None:
        return math.nan
    return safe_float(row.get("close"))


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


def score_universe(date: pd.Timestamp, histories: dict[str, pd.DataFrame], names: dict[str, str], args: argparse.Namespace) -> list[dict[str, Any]]:
    scored = []
    for code, hist in histories.items():
        idx, row = row_at_or_before(hist, date)
        if row is None:
            continue
        item = score_at(hist, idx, args.min_amount, args.min_price)
        if item is None:
            continue
        scored.append({"code": code, "name": names.get(code, code), "date": date, **item})
    return sorted(scored, key=lambda item: item["proxy_score"], reverse=True)


@dataclass
class Account:
    strategy: str
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    histories: dict[str, pd.DataFrame] = field(default_factory=dict)


def account_value(account: Account, date: pd.Timestamp) -> float:
    value = account.cash
    for code, shares in account.positions.items():
        close = price_at(account.histories.get(code, pd.DataFrame()), date)
        if not math.isnan(close) and close > 0:
            value += shares * close
    return value


def rebalance_account(account: Account, target_codes: list[str], date: pd.Timestamp, args: argparse.Namespace) -> dict[str, Any]:
    unique_targets = []
    for code in target_codes:
        if code in account.histories and code not in unique_targets:
            close = price_at(account.histories[code], date)
            if not math.isnan(close) and close > 0:
                unique_targets.append(code)

    current_value = account_value(account, date)
    if current_value <= 0:
        return {"turnover": 0.0, "trade_cost": 0.0, "buy_value": 0.0, "sell_value": 0.0}

    current_values: dict[str, float] = {}
    for code, shares in account.positions.items():
        close = price_at(account.histories.get(code, pd.DataFrame()), date)
        if not math.isnan(close) and close > 0:
            current_values[code] = shares * close

    if unique_targets:
        equal_target = current_value / len(unique_targets)
        target_values = {code: equal_target for code in unique_targets}
    else:
        target_values = {}

    all_codes = set(current_values) | set(target_values)
    buy_value = 0.0
    sell_value = 0.0
    for code in all_codes:
        diff = target_values.get(code, 0.0) - current_values.get(code, 0.0)
        if diff > 0:
            buy_value += diff
        else:
            sell_value += abs(diff)

    trade_cost = (buy_value + sell_value) * args.commission_rate + sell_value * args.stamp_duty_rate
    net_value = max(0.0, current_value - trade_cost)
    account.positions = {}
    if unique_targets:
        equal_target = net_value / len(unique_targets)
        for code in unique_targets:
            close = price_at(account.histories[code], date)
            account.positions[code] = equal_target / close
        account.cash = 0.0
    else:
        account.cash = net_value

    turnover = (buy_value + sell_value) / current_value if current_value > 0 else 0.0
    return {
        "turnover": turnover,
        "trade_cost": trade_cost,
        "buy_value": buy_value,
        "sell_value": sell_value,
    }


def index_curve(name: str, hist: pd.DataFrame, calendar: list[pd.Timestamp], initial_capital: float) -> pd.DataFrame:
    rows = []
    start_close = math.nan
    for date in calendar:
        close = price_at(hist, date)
        if math.isnan(close) or close <= 0:
            continue
        if math.isnan(start_close):
            start_close = close
        equity = initial_capital * close / start_close
        rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "strategy": name,
                "equity": round(equity, 2),
                "cash": 0.0,
                "holding_count": 1,
                "holdings": name,
                "rebalance": False,
                "turnover": 0.0,
                "trade_cost": 0.0,
            }
        )
    return pd.DataFrame(rows)


def run_capital_backtest(
    args: argparse.Namespace,
    histories: dict[str, pd.DataFrame],
    names: dict[str, str],
    leader_histories: dict[str, pd.DataFrame],
    indexes: dict[str, pd.DataFrame],
    analysis_start_dt: datetime,
    end_dt: datetime,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    calendar = sorted(
        date
        for date in set().union(*[set(hist["date"].tolist()) for hist in histories.values()])
        if pd.Timestamp(analysis_start_dt) <= date <= pd.Timestamp(end_dt)
    )
    rebalances = set(rebalance_dates(calendar, args.rebalance))
    scored_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}

    accounts = {
        "proxy_top5_always_in": Account("proxy_top5_always_in", args.initial_capital, histories=histories),
        f"threshold_{int(args.threshold)}_cash_when_none": Account(f"threshold_{int(args.threshold)}_cash_when_none", args.initial_capital, histories=histories),
        "leader_basket_equal": Account("leader_basket_equal", args.initial_capital, histories=leader_histories),
    }
    records: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []

    for date in calendar:
        is_rebalance = date in rebalances
        trade_info_by_strategy: dict[str, dict[str, Any]] = {}
        if is_rebalance:
            scored = score_universe(date, histories, names, args)
            scored_by_date[date] = scored
            top_codes = [item["code"] for item in scored[: args.top_n]]
            threshold_items = [item for item in scored if item["proxy_score"] >= args.threshold][: args.top_n]
            threshold_codes = [item["code"] for item in threshold_items]
            leader_codes = [code for code in LEADER_BASKET if code in leader_histories]

            targets = {
                "proxy_top5_always_in": top_codes,
                f"threshold_{int(args.threshold)}_cash_when_none": threshold_codes,
                "leader_basket_equal": leader_codes,
            }
            for strategy, account in accounts.items():
                trade_info_by_strategy[strategy] = rebalance_account(account, targets[strategy], date, args)

            decision_rows.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "available_scored_count": len(scored),
                    "top5_codes": ",".join(top_codes),
                    "threshold_codes": ",".join(threshold_codes),
                    "threshold_count": len(threshold_codes),
                    "threshold_min_score": round(args.threshold, 2),
                    "best_score": round(scored[0]["proxy_score"], 2) if scored else "",
                }
            )

        for strategy, account in accounts.items():
            value = account_value(account, date)
            info = trade_info_by_strategy.get(strategy, {})
            holding_names = []
            for code in account.positions:
                holding_names.append(f"{code}{names.get(code, LEADER_BASKET.get(code, ''))}")
            records.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "strategy": strategy,
                    "equity": round(value, 2),
                    "cash": round(account.cash, 2),
                    "holding_count": len(account.positions),
                    "holdings": ",".join(holding_names),
                    "rebalance": is_rebalance,
                    "turnover": round(safe_float(info.get("turnover"), 0.0), 6),
                    "trade_cost": round(safe_float(info.get("trade_cost"), 0.0), 2),
                    "buy_value": round(safe_float(info.get("buy_value"), 0.0), 2),
                    "sell_value": round(safe_float(info.get("sell_value"), 0.0), 2),
                }
            )

    equity = pd.DataFrame(records)
    if "沪深300" in indexes:
        equity = pd.concat([equity, index_curve("CSI300_buy_hold", indexes["沪深300"], calendar, args.initial_capital)], ignore_index=True)
    decisions = pd.DataFrame(decision_rows)
    return equity, decisions


def max_drawdown(values: pd.Series) -> float:
    if values.empty:
        return math.nan
    running_max = values.cummax()
    drawdowns = values / running_max - 1
    return float(drawdowns.min() * 100)


def period_metrics(curve: pd.DataFrame, start_date: pd.Timestamp, end_date: pd.Timestamp, period_name: str, initial_capital: float) -> list[dict[str, Any]]:
    rows = []
    if curve.empty:
        return rows
    frame = curve.copy()
    frame["date_dt"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[(frame["date_dt"] >= start_date) & (frame["date_dt"] <= end_date)]
    for strategy, part in frame.groupby("strategy"):
        part = part.sort_values("date_dt")
        equity = pd.to_numeric(part["equity"], errors="coerce").dropna()
        if len(equity) < 2:
            continue
        actual_start = part.iloc[0]["date_dt"]
        actual_end = part.iloc[-1]["date_dt"]
        start_value = float(equity.iloc[0])
        end_value = float(equity.iloc[-1])
        days = max(1, int((actual_end - actual_start).days))
        total_return = end_value / start_value - 1
        annualized = (end_value / start_value) ** (365.25 / days) - 1 if start_value > 0 else math.nan
        normalized_end_value = initial_capital * end_value / start_value if start_value > 0 else math.nan
        holdings = pd.to_numeric(part["holding_count"], errors="coerce").dropna()
        rebalances = part[part["rebalance"].astype(str).str.lower().isin(["true", "1"])]
        turnover = pd.to_numeric(rebalances["turnover"], errors="coerce").dropna()
        cost = pd.to_numeric(part["trade_cost"], errors="coerce").dropna()
        rows.append(
            {
                "period": period_name,
                "strategy": strategy,
                "start_date": actual_start.strftime("%Y-%m-%d"),
                "end_date": actual_end.strftime("%Y-%m-%d"),
                "initial_capital": round(initial_capital, 2),
                "start_value": round(start_value, 2),
                "end_value": round(end_value, 2),
                "normalized_start_value": round(initial_capital, 2),
                "normalized_end_value": round(normalized_end_value, 2) if not math.isnan(normalized_end_value) else "",
                "profit": round(end_value - start_value, 2),
                "normalized_profit": round(normalized_end_value - initial_capital, 2) if not math.isnan(normalized_end_value) else "",
                "total_return_pct": round(total_return * 100, 2),
                "annualized_return_pct": round(annualized * 100, 2) if not math.isnan(annualized) else "",
                "max_drawdown_pct": round(max_drawdown(equity), 2),
                "avg_holding_count": round(float(holdings.mean()), 2) if not holdings.empty else "",
                "median_holding_count": round(float(holdings.median()), 2) if not holdings.empty else "",
                "min_holding_count": int(holdings.min()) if not holdings.empty else "",
                "max_holding_count": int(holdings.max()) if not holdings.empty else "",
                "cash_day_rate_pct": round(float((holdings == 0).mean() * 100), 2) if not holdings.empty else "",
                "rebalance_count": len(rebalances),
                "avg_turnover_on_rebalance_pct": round(float(turnover.mean() * 100), 2) if not turnover.empty else "",
                "total_trade_cost": round(float(cost.sum()), 2) if not cost.empty else "",
            }
        )
    return rows


def yearly_metrics(curve: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if curve.empty:
        return pd.DataFrame()
    frame = curve.copy()
    frame["date_dt"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["year"] = frame["date_dt"].dt.year
    rows = []
    for (strategy, year), part in frame.groupby(["strategy", "year"]):
        rows.extend(period_metrics(part, part["date_dt"].min(), part["date_dt"].max(), str(int(year)), initial_capital))
    return pd.DataFrame(rows)


def summarize_capital(curve: pd.DataFrame, args: argparse.Namespace, analysis_start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    start = pd.Timestamp(analysis_start_dt)
    end = pd.Timestamp(end_dt)
    rows = period_metrics(curve, start, end, f"full_{args.years}y", args.initial_capital)
    last_1y_start = max(start, end - pd.DateOffset(years=1))
    rows.extend(period_metrics(curve, last_1y_start, end, "last_1y", args.initial_capital))
    return pd.DataFrame(rows)


def load_histories(args: argparse.Namespace) -> tuple[dict[str, pd.DataFrame], dict[str, str], dict[str, pd.DataFrame], dict[str, pd.DataFrame], list[str], dict[str, Any], datetime, datetime]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    universe = load_universe(Path(args.learning_pool_dir), args.max_codes)
    end_dt = datetime.strptime(args.date, "%Y%m%d")
    analysis_start_dt = end_dt.replace(year=end_dt.year - args.years)
    fetch_start_dt = analysis_start_dt - pd.Timedelta(days=260)
    start_date = fetch_start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    histories: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    names = {str(row["code"]).zfill(6): safe_text(row["name"]) for _, row in universe.iterrows()}
    for code, name in names.items():
        print(f"[history] {code} {name}", flush=True)
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
        else:
            errors.append(f"{code}: {error}")
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

    meta = {
        "run_date": args.date,
        "analysis_start_date": analysis_start_dt.strftime("%Y%m%d"),
        "fetch_start_date": start_date,
        "end_date": end_date,
        "years": args.years,
        "initial_capital": args.initial_capital,
        "universe_codes": len(names),
        "loaded_histories": len(histories),
        "leader_histories": len(leader_histories),
        "top_n": args.top_n,
        "threshold": args.threshold,
        "rebalance": args.rebalance,
        "commission_rate": args.commission_rate,
        "stamp_duty_rate": args.stamp_duty_rate,
        "method_note": "资金账户组合回测：真实历史前复权日线 + 月度调仓 + 等权持仓；分数是价格/流动性/不过热/波动/回撤构成的代理分，不是过去真实 AI 分。",
    }
    return histories, names, leader_histories, indexes, errors, meta, analysis_start_dt, end_dt


def write_report(path: Path, capital_summary: pd.DataFrame, yearly: pd.DataFrame, decisions: pd.DataFrame, errors: list[str], meta: dict[str, Any]) -> None:
    threshold_name = f"threshold_{int(meta['threshold'])}_cash_when_none"
    lines = [
        f"# 资金账户历史回测 - {datetime.strptime(meta['run_date'], '%Y%m%d'):%Y-%m-%d}",
        "",
        "这份报告回答的问题是：如果用 10 万元起始资金，按规则真实持仓、换仓、空仓，过去一年和五年大约会发生什么。",
        "",
        "## 回测设定",
        f"- 起始资金：{meta['initial_capital']:.0f} 元。",
        f"- 时间：{meta['analysis_start_date']} 至 {meta['end_date']}，约 {meta['years']} 年；另单独统计最近 1 年。",
        f"- 候选池：当前基本面学习池前 {meta['universe_codes']} 只，成功加载历史行情 {meta['loaded_histories']} 只。",
        f"- 调仓：{'月度' if meta['rebalance'] == 'M' else '周度'}，每次最多持有 {meta['top_n']} 只。",
        f"- 成本：买卖佣金 {meta['commission_rate']:.4%}，卖出印花税 {meta['stamp_duty_rate']:.4%}。",
        "",
        "## 策略说明",
        "- `threshold_80_cash_when_none`：只买代理分 >= 80 的股票，最多 5 只；没有达标股票就空仓。这是更接近你要的“有机会才出手”。",
        "- `proxy_top5_always_in`：每月强行买入评分前 5 名。它用于观察高换手和强制交易的代价，不建议直接当实盘策略。",
        "- `leader_basket_equal`：行业龙头篮子等权持有，用来对比“买龙头不折腾”。",
        "- `CSI300_buy_hold`：沪深300买入并持有，用来对比指数。",
        "",
        "## 资金结果",
        "| 周期 | 策略 | 账户期初 | 账户期末 | 10万起算期末 | 收益 | 年化 | 最大回撤 | 平均持股数 | 空仓天数占比 | 平均调仓换手 | 总交易成本 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if capital_summary.empty:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - |")
    else:
        sort_order = {f"full_{meta['years']}y": 0, "last_1y": 1}
        frame = capital_summary.copy()
        frame["_order"] = frame["period"].map(lambda value: sort_order.get(value, 9))
        frame = frame.sort_values(["_order", "strategy"])
        for row in frame.itertuples(index=False):
            lines.append(
                f"| {row.period} | {row.strategy} | {row.start_value} | {row.end_value} | {row.normalized_end_value} | {row.total_return_pct}% | {row.annualized_return_pct}% | {row.max_drawdown_pct}% | {row.avg_holding_count} | {row.cash_day_rate_pct}% | {row.avg_turnover_on_rebalance_pct}% | {row.total_trade_cost} |"
            )

    lines += [
        "",
        "## 对当前问题的直接读法",
    ]
    if not capital_summary.empty:
        full = capital_summary[(capital_summary["period"] == f"full_{meta['years']}y") & (capital_summary["strategy"] == threshold_name)]
        last = capital_summary[(capital_summary["period"] == "last_1y") & (capital_summary["strategy"] == threshold_name)]
        if not full.empty:
            row = full.iloc[0]
            lines.append(
                f"- 严格门槛策略过去约 {meta['years']} 年：10 万变为 {row['normalized_end_value']} 元，年化 {row['annualized_return_pct']}%，平均持股 {row['avg_holding_count']} 只，最大回撤 {row['max_drawdown_pct']}%。"
            )
        if not last.empty:
            row = last.iloc[0]
            lines.append(
                f"- 最近 1 年若重新用 10 万起算：期末约 {row['normalized_end_value']} 元，年化 {row['annualized_return_pct']}%，平均持股 {row['avg_holding_count']} 只，空仓天数占比 {row['cash_day_rate_pct']}%。"
            )
        forced = capital_summary[(capital_summary["period"] == f"full_{meta['years']}y") & (capital_summary["strategy"] == "proxy_top5_always_in")]
        if not forced.empty:
            row = forced.iloc[0]
            lines.append(
                f"- 强制 Top5 策略的平均调仓换手为 {row['avg_turnover_on_rebalance_pct']}%，它用来判断系统是否会变成频繁换票。"
            )
    else:
        lines.append("- 样本不足，无法给出资金账户结论。")

    lines += [
        "",
        "## 年度拆分",
        "| 年份 | 策略 | 年初 | 年末 | 收益 | 最大回撤 | 平均持股数 | 空仓天数占比 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    if yearly.empty:
        lines.append("| - | - | - | - | - | - | - | - |")
    else:
        for row in yearly.sort_values(["period", "strategy"]).itertuples(index=False):
            lines.append(
                f"| {row.period} | {row.strategy} | {row.start_value} | {row.end_value} | {row.total_return_pct}% | {row.max_drawdown_pct}% | {row.avg_holding_count} | {row.cash_day_rate_pct}% |"
            )

    lines += [
        "",
        "## 达标持仓次数",
        "| 指标 | 数值 |",
        "|---|---:|",
    ]
    if decisions.empty:
        lines.append("| 调仓次数 | 0 |")
    else:
        threshold_counts = pd.to_numeric(decisions["threshold_count"], errors="coerce").dropna()
        empty_rate = float((threshold_counts == 0).mean() * 100) if not threshold_counts.empty else math.nan
        lines.append(f"| 调仓次数 | {len(decisions)} |")
        lines.append(f"| 达标股票平均数量 | {threshold_counts.mean():.2f} |")
        lines.append(f"| 无达标股票调仓占比 | {empty_rate:.2f}% |")
        lines.append(f"| 单次最多达标股票数 | {int(threshold_counts.max()) if not threshold_counts.empty else 0} |")

    lines += [
        "",
        "## 使用限制",
        "- 这是当前学习池的历史压力测试，仍有当前成分池带来的幸存者偏差。",
        "- 历史评分是可量化代理分，不是过去每天真实调用大模型生成的 AI 分。",
        "- 前复权价格适合估算收益曲线，但不是逐笔成交级别回测；滑点、涨跌停无法成交、停牌、100 股整数手等还没有完全模拟。",
        "- 如果这个版本跑不赢指数/龙头/随机，说明策略逻辑必须降级；如果跑赢，也只能说明值得进入更严格的全市场无偏回测。",
    ]
    if errors:
        lines += ["", "## 数据源提醒"]
        for error in errors[:50]:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    histories, names, leader_histories, indexes, errors, meta, analysis_start_dt, end_dt = load_histories(args)
    equity, decisions = run_capital_backtest(args, histories, names, leader_histories, indexes, analysis_start_dt, end_dt)
    capital_summary = summarize_capital(equity, args, analysis_start_dt, end_dt)
    yearly = yearly_metrics(equity, args.initial_capital)

    date_key = args.date
    equity.to_csv(output_dir / f"portfolio_equity_curve_{date_key}.csv", index=False, encoding="utf-8-sig")
    decisions.to_csv(output_dir / f"portfolio_rebalance_decisions_{date_key}.csv", index=False, encoding="utf-8-sig")
    capital_summary.to_csv(output_dir / f"portfolio_capital_summary_{date_key}.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(output_dir / f"portfolio_yearly_summary_{date_key}.csv", index=False, encoding="utf-8-sig")
    (output_dir / f"portfolio_backtest_meta_{date_key}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = report_dir / f"portfolio_backtest_{date_key}.md"
    write_report(report_path, capital_summary, yearly, decisions, errors, meta)
    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "equity_rows": len(equity),
                "summary_rows": len(capital_summary),
                "yearly_rows": len(yearly),
                "decisions": len(decisions),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
