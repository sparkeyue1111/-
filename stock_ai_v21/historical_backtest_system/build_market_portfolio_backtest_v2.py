#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from build_portfolio_backtest import (
    Account,
    INDEX_SYMBOLS,
    LEADER_BASKET,
    account_value,
    fetch_index_history,
    fetch_stock_history,
    index_curve,
    normalize_code,
    rebalance_account,
    rebalance_dates,
    row_at_or_before,
    safe_float,
    run_capital_backtest,
    safe_text,
    score_at,
    summarize_capital,
    yearly_metrics,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V2 full-market capital-account backtest without learning-pool universe bias.")
    parser.add_argument("--output-dir", default="/app/data/historical_backtest")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--years", type=int, default=int(os.environ.get("BACKTEST_YEARS", "5")))
    parser.add_argument("--max-codes", type=int, default=int(os.environ.get("BACKTEST_V2_MAX_CODES", "0")))
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("BACKTEST_TOP_N", "5")))
    parser.add_argument("--score-version", choices=["v1", "v21"], default=os.environ.get("BACKTEST_SCORE_VERSION", "v21"))
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--hold-threshold", type=float, default=None)
    parser.add_argument("--max-new-per-rebalance", type=int, default=int(os.environ.get("BACKTEST_MAX_NEW_PER_REBALANCE", "2")))
    parser.add_argument("--market-guard", action=argparse.BooleanOptionalAction, default=os.environ.get("BACKTEST_MARKET_GUARD", "1") != "0")
    parser.add_argument("--sticky-hold", action=argparse.BooleanOptionalAction, default=os.environ.get("BACKTEST_STICKY_HOLD", "1") != "0")
    parser.add_argument("--rebalance", choices=["M", "W"], default=os.environ.get("BACKTEST_REBALANCE", "M"))
    parser.add_argument("--initial-capital", type=float, default=float(os.environ.get("BACKTEST_INITIAL_CAPITAL", "100000")))
    parser.add_argument("--commission-rate", type=float, default=float(os.environ.get("BACKTEST_COMMISSION_RATE", "0.0003")))
    parser.add_argument("--stamp-duty-rate", type=float, default=float(os.environ.get("BACKTEST_STAMP_DUTY_RATE", "0.0005")))
    parser.add_argument("--min-amount", type=float, default=float(os.environ.get("BACKTEST_MIN_AMOUNT", "100000000")))
    parser.add_argument("--min-price", type=float, default=float(os.environ.get("BACKTEST_MIN_PRICE", "3")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "12")))
    parser.add_argument("--sleep-sec", type=float, default=float(os.environ.get("BACKTEST_V2_SLEEP_SEC", "0.05")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("BACKTEST_V2_WORKERS", "1")))
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--force-universe-refresh", action="store_true")
    parser.add_argument("--include-bse", action="store_true")
    args = parser.parse_args()
    if args.threshold is None:
        default_threshold = "76" if args.score_version == "v21" else "80"
        args.threshold = float(os.environ.get("BACKTEST_PORTFOLIO_THRESHOLD", default_threshold))
    if args.hold_threshold is None:
        default_hold_threshold = str(float(args.threshold) - 8.0)
        args.hold_threshold = float(os.environ.get("BACKTEST_HOLD_THRESHOLD", default_hold_threshold))
    return args


def score_proxy_v21(item: dict[str, Any], name: str) -> float:
    stock_name = safe_text(name)
    if "ST" in stock_name.upper() or "退" in stock_name:
        return 0.0

    ret20 = float(item.get("ret20", 0.0) or 0.0)
    ret60 = float(item.get("ret60", 0.0) or 0.0)
    ret120 = float(item.get("ret120", 0.0) or 0.0)
    vol60 = float(item.get("vol60", 0.0) or 0.0)
    drawdown120 = float(item.get("drawdown120", 0.0) or 0.0)
    liquidity_score = float(item.get("liquidity_score", 0.0) or 0.0)

    score = 55.0
    score += max(-25.0, min(45.0, ret60)) * 0.65
    score += max(-30.0, min(90.0, ret120)) * 0.25
    score += (max(0.0, min(100.0, liquidity_score)) - 50.0) * 0.12

    score -= max(0.0, ret20 - 18.0) * 0.90
    score -= max(0.0, ret60 - 50.0) * 0.80
    score -= max(0.0, ret120 - 110.0) * 0.35
    score -= max(0.0, vol60 - 45.0) * 0.75
    score -= max(0.0, -drawdown120 - 28.0) * 0.90

    if drawdown120 > -3.0 and ret60 > 25.0:
        score -= 10.0
    if 8.0 <= ret60 <= 42.0 and 5.0 <= ret120 <= 90.0 and -22.0 <= drawdown120 <= -5.0 and vol60 <= 55.0 and ret20 <= 18.0:
        score += 8.0
    if ret60 < 0.0 or ret120 < -8.0:
        score -= 12.0

    return round(max(0.0, min(100.0, score)), 4)


def normalize_date(value: Any) -> str:
    text = safe_text(value)
    if not text or text in {"-", "--", "nan", "None"}:
        return ""
    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


def normalize_stock_code(value: Any) -> str:
    text = safe_text(value)
    if not text:
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def fetch_sse_list(symbol: str, timeout: int) -> pd.DataFrame:
    indicator_map = {"main": "1", "kcb": "8"}
    url = "https://query.sse.com.cn/sseQuery/commonQuery.do"
    headers = {
        "Referer": "https://www.sse.com.cn/assortment/stock/list/share/",
        "User-Agent": "Mozilla/5.0",
    }
    params = {
        "STOCK_TYPE": indicator_map[symbol],
        "REG_PROVINCE": "",
        "CSRC_CODE": "",
        "STOCK_CODE": "",
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
        "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams",
        "isPagination": "true",
        "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.endPage": "1",
    }
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json().get("result", [])
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(columns=["code", "name", "market", "board", "list_date", "delist_date"])
    out = pd.DataFrame()
    out["code"] = frame["A_STOCK_CODE"].map(normalize_stock_code)
    out["name"] = frame["SEC_NAME_CN"].map(safe_text)
    out["market"] = "SH"
    out["board"] = "科创板" if symbol == "kcb" else "沪主板"
    out["list_date"] = frame.get("LIST_DATE", "").map(normalize_date)
    out["delist_date"] = frame.get("DELIST_DATE", "").map(normalize_date)
    return out


def fetch_szse_list(timeout: int) -> pd.DataFrame:
    url = "https://www.szse.cn/api/report/ShowReport"
    params = {
        "SHOWTYPE": "xlsx",
        "CATALOGID": "1110",
        "TABKEY": "tab1",
        "random": str(time.time()),
    }
    response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    frame = pd.read_excel(BytesIO(response.content))
    if frame.empty:
        return pd.DataFrame(columns=["code", "name", "market", "board", "list_date", "delist_date"])
    out = pd.DataFrame()
    out["code"] = frame["A股代码"].map(normalize_stock_code)
    out["name"] = frame["A股简称"].map(safe_text)
    out["market"] = "SZ"
    out["board"] = frame.get("板块", "").map(safe_text)
    out["list_date"] = frame.get("A股上市日期", "").map(normalize_date)
    out["delist_date"] = ""
    return out


def fetch_akshare_code_name(timeout: int) -> pd.DataFrame:
    import akshare as ak

    # The wrapper can be slow but is still a useful fallback. It does not include listing dates.
    _ = timeout
    frame = ak.stock_info_a_code_name()
    out = pd.DataFrame()
    out["code"] = frame["code"].map(normalize_stock_code)
    out["name"] = frame["name"].map(safe_text)
    out["market"] = out["code"].map(lambda code: "SH" if code.startswith("6") else ("BJ" if code.startswith(("4", "8")) else "SZ"))
    out["board"] = ""
    out["list_date"] = ""
    out["delist_date"] = ""
    return out


def load_market_universe(output_dir: Path, args: argparse.Namespace) -> tuple[pd.DataFrame, list[str]]:
    cache_path = output_dir / "cache" / "universe" / "market_universe_v2.csv"
    errors: list[str] = []
    if cache_path.exists() and not args.force_universe_refresh:
        frame = pd.read_csv(cache_path, dtype={"code": str})
    else:
        parts = []
        for symbol in ["main", "kcb"]:
            try:
                parts.append(fetch_sse_list(symbol, args.timeout))
            except Exception as exc:
                errors.append(f"SSE {symbol}: {type(exc).__name__}: {str(exc)[:180]}")
        try:
            parts.append(fetch_szse_list(args.timeout))
        except Exception as exc:
            errors.append(f"SZSE: {type(exc).__name__}: {str(exc)[:180]}")
        if not parts:
            try:
                parts.append(fetch_akshare_code_name(args.timeout))
            except Exception as exc:
                errors.append(f"AKShare code-name fallback: {type(exc).__name__}: {str(exc)[:180]}")
        if not parts:
            raise SystemExit("unable to load market universe")
        frame = pd.concat(parts, ignore_index=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_path, index=False, encoding="utf-8-sig")

    frame["code"] = frame["code"].map(normalize_stock_code)
    frame["name"] = frame["name"].map(safe_text)
    frame = frame[frame["code"].str.match(r"^\d{6}$", na=False)]
    if not args.include_bse:
        frame = frame[~frame["code"].str.startswith(("4", "8"))]
    frame = frame.drop_duplicates("code").sort_values("code").reset_index(drop=True)
    if args.max_codes and args.max_codes > 0:
        frame = frame.head(args.max_codes)
    return frame, errors


def fetch_history_task(task: tuple[str, str, str, str, str, dict[str, Any]]) -> tuple[str, str, pd.DataFrame, str]:
    code, name, start_date, end_date, cache_dir_text, args_dict = task
    worker_args = argparse.Namespace(**args_dict)
    hist, error = fetch_stock_history(code, start_date, end_date, Path(cache_dir_text), worker_args)
    return code, name, hist, error


def score_history_task(task: tuple[str, str, str, str, str, dict[str, Any], list[str]]) -> tuple[str, str, list[dict[str, Any]], str, int]:
    code, name, start_date, end_date, cache_dir_text, args_dict, rebalance_texts = task
    worker_args = argparse.Namespace(**args_dict)
    hist, error = fetch_stock_history(code, start_date, end_date, Path(cache_dir_text), worker_args)
    if error:
        return code, name, [], error, 0
    if len(hist) < 180:
        return code, name, [], f"history too short ({len(hist)})", len(hist)
    rows: list[dict[str, Any]] = []
    for date_text in rebalance_texts:
        rebalance_date = pd.Timestamp(date_text)
        idx, row = row_at_or_before(hist, rebalance_date)
        if row is None:
            continue
        item = score_at(hist, idx, worker_args.min_amount, worker_args.min_price)
        if item is None:
            continue
        proxy_score_v1 = float(item["proxy_score"])
        if getattr(worker_args, "score_version", "v1") == "v21":
            item["proxy_score"] = score_proxy_v21(item, name)
        rows.append(
            {
                "date": rebalance_date.strftime("%Y-%m-%d"),
                "code": code,
                "name": name,
                "close": round(float(item["close"]), 4),
                "avg_amount_20": round(float(item["avg_amount_20"]), 2),
                "ret20": round(float(item["ret20"]), 4),
                "ret60": round(float(item["ret60"]), 4),
                "ret120": round(float(item["ret120"]), 4),
                "vol60": round(float(item["vol60"]), 4) if not math.isnan(float(item["vol60"])) else "",
                "drawdown120": round(float(item["drawdown120"]), 4),
                "proxy_score": round(float(item["proxy_score"]), 4),
                "proxy_score_v1": round(proxy_score_v1, 4),
                "score_version": getattr(worker_args, "score_version", "v1"),
                "liquidity_score": round(float(item["liquidity_score"]), 4),
                "trend_score": round(float(item["trend_score"]), 4),
                "heat_score": round(float(item["heat_score"]), 4),
                "risk_score": round(float(item["risk_score"]), 4),
            }
        )
    return code, name, rows, "", len(hist)


def load_indexes(args: argparse.Namespace, start_date: str, end_date: str, cache_dir: Path, errors: list[str]) -> dict[str, pd.DataFrame]:
    indexes: dict[str, pd.DataFrame] = {}
    for name, symbol in INDEX_SYMBOLS.items():
        hist, error = fetch_index_history(name, symbol, start_date, end_date, cache_dir, args)
        if error:
            errors.append(error)
        if not hist.empty:
            indexes[name] = hist
    return indexes


def load_selected_histories(
    codes: set[str],
    names: dict[str, str],
    start_date: str,
    end_date: str,
    cache_dir: Path,
    args: argparse.Namespace,
    errors: list[str],
) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    total = len(codes)
    for idx, code in enumerate(sorted(codes), start=1):
        if idx == 1 or idx % 100 == 0 or idx == total:
            print(f"[selected-history] {idx}/{total} loaded={len(histories)} latest={code} {names.get(code, '')}", flush=True)
        hist, error = fetch_stock_history(code, start_date, end_date, cache_dir, args)
        if error:
            errors.append(f"{code}: {error}")
            continue
        if len(hist) >= 180:
            histories[code] = hist
    return histories


def build_decisions_from_scores(scores: pd.DataFrame, rebalances: list[pd.Timestamp], args: argparse.Namespace) -> tuple[pd.DataFrame, set[str]]:
    decision_rows: list[dict[str, Any]] = []
    selected_codes: set[str] = set()
    if scores.empty:
        return pd.DataFrame(), selected_codes
    for rebalance_date in rebalances:
        date_text = rebalance_date.strftime("%Y-%m-%d")
        part = scores[scores["date"] == date_text].copy()
        if part.empty:
            decision_rows.append(
                {
                    "date": date_text,
                    "available_scored_count": 0,
                    "top5_codes": "",
                    "threshold_codes": "",
                    "threshold_count": 0,
                    "threshold_min_score": round(args.threshold, 2),
                    "best_score": "",
                }
            )
            continue
        part = part.sort_values("proxy_score", ascending=False)
        top = part.head(args.top_n)
        threshold = part[part["proxy_score"] >= args.threshold].head(args.top_n)
        top_codes = top["code"].astype(str).tolist()
        threshold_codes = threshold["code"].astype(str).tolist()
        selected_codes.update(top_codes)
        selected_codes.update(threshold_codes)
        decision_rows.append(
            {
                "date": date_text,
                "available_scored_count": len(part),
                "top5_codes": ",".join(top_codes),
                "threshold_codes": ",".join(threshold_codes),
                "threshold_count": len(threshold_codes),
                "threshold_min_score": round(args.threshold, 2),
                "best_score": round(float(part.iloc[0]["proxy_score"]), 2),
            }
        )
    return pd.DataFrame(decision_rows), selected_codes


def market_state(index_hist: pd.DataFrame, date: pd.Timestamp) -> dict[str, Any]:
    idx, row = row_at_or_before(index_hist, date)
    if row is None or idx < 200:
        return {"ok": False, "close": math.nan, "ma200": math.nan, "ret120": math.nan}
    close = float(row["close"])
    ma200 = float(pd.to_numeric(index_hist.iloc[idx - 199 : idx + 1]["close"], errors="coerce").mean())
    close120 = safe_float(index_hist.iloc[idx - 120].get("close")) if idx >= 120 else math.nan
    ret120 = close / close120 - 1 if close120 and close120 > 0 else math.nan
    ok = bool(close >= ma200 and (math.isnan(ret120) or ret120 > -0.10))
    return {"ok": ok, "close": close, "ma200": ma200, "ret120": ret120}


def build_guarded_decisions_from_scores(
    scores: pd.DataFrame,
    rebalances: list[pd.Timestamp],
    index_hist: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, set[str]]:
    decision_rows: list[dict[str, Any]] = []
    selected_codes: set[str] = set()
    holdings: list[str] = []
    if scores.empty:
        return pd.DataFrame(), selected_codes

    for rebalance_date in rebalances:
        date_text = rebalance_date.strftime("%Y-%m-%d")
        part = scores[scores["date"] == date_text].copy()
        state = market_state(index_hist, rebalance_date)
        if part.empty:
            holdings = []
            decision_rows.append(
                {
                    "date": date_text,
                    "available_scored_count": 0,
                    "top5_codes": "",
                    "threshold_codes": "",
                    "threshold_count": 0,
                    "threshold_min_score": round(args.threshold, 2),
                    "best_score": "",
                    "market_ok": state["ok"],
                    "index_close": "",
                    "index_ma200": "",
                    "index_ret120_pct": "",
                }
            )
            continue

        part = part.sort_values("proxy_score", ascending=False)
        top = part.head(args.top_n)
        top_codes = top["code"].astype(str).tolist()
        threshold_codes: list[str]
        if args.market_guard and not state["ok"]:
            threshold_codes = []
            holdings = []
        elif args.sticky_hold:
            score_map = {str(row["code"]): float(row["proxy_score"]) for _, row in part.iterrows()}
            keep = [code for code in holdings if score_map.get(code, -999.0) >= args.hold_threshold]
            threshold_pool = part[part["proxy_score"] >= args.threshold]["code"].astype(str).tolist()
            new_candidates = [code for code in threshold_pool if code not in keep]
            allowed_new = max(0, min(args.max_new_per_rebalance, args.top_n - len(keep)))
            threshold_codes = (keep + new_candidates[:allowed_new])[: args.top_n]
            holdings = threshold_codes
        else:
            threshold = part[part["proxy_score"] >= args.threshold].head(args.top_n)
            threshold_codes = threshold["code"].astype(str).tolist()
            holdings = threshold_codes

        selected_codes.update(top_codes)
        selected_codes.update(threshold_codes)
        decision_rows.append(
            {
                "date": date_text,
                "available_scored_count": len(part),
                "top5_codes": ",".join(top_codes),
                "threshold_codes": ",".join(threshold_codes),
                "threshold_count": len(threshold_codes),
                "threshold_min_score": round(args.threshold, 2),
                "best_score": round(float(part.iloc[0]["proxy_score"]), 2),
                "market_ok": state["ok"],
                "index_close": round(float(state["close"]), 4) if not math.isnan(float(state["close"])) else "",
                "index_ma200": round(float(state["ma200"]), 4) if not math.isnan(float(state["ma200"])) else "",
                "index_ret120_pct": round(float(state["ret120"]) * 100, 4) if not math.isnan(float(state["ret120"])) else "",
            }
        )
    return pd.DataFrame(decision_rows), selected_codes


def split_codes(text: Any) -> list[str]:
    return [normalize_stock_code(part) for part in safe_text(text).split(",") if normalize_stock_code(part)]


def run_capital_from_decisions(
    args: argparse.Namespace,
    decisions: pd.DataFrame,
    selected_histories: dict[str, pd.DataFrame],
    names: dict[str, str],
    leader_histories: dict[str, pd.DataFrame],
    indexes: dict[str, pd.DataFrame],
    analysis_start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    if "沪深300" in indexes and not indexes["沪深300"].empty:
        calendar = [
            date
            for date in indexes["沪深300"]["date"].tolist()
            if pd.Timestamp(analysis_start_dt) <= date <= pd.Timestamp(end_dt)
        ]
    else:
        calendar = sorted(
            date
            for date in set().union(*[set(hist["date"].tolist()) for hist in selected_histories.values()])
            if pd.Timestamp(analysis_start_dt) <= date <= pd.Timestamp(end_dt)
        )
    decision_map = {safe_text(row["date"]): row for _, row in decisions.iterrows()}
    rebalances = set(pd.to_datetime(list(decision_map.keys()), errors="coerce").dropna().tolist())
    threshold_strategy = f"threshold_{int(args.threshold)}_cash_when_none"
    accounts = {
        "proxy_top5_always_in": Account("proxy_top5_always_in", args.initial_capital, histories=selected_histories),
        threshold_strategy: Account(threshold_strategy, args.initial_capital, histories=selected_histories),
        "leader_basket_equal": Account("leader_basket_equal", args.initial_capital, histories=leader_histories),
    }
    records: list[dict[str, Any]] = []
    for date in calendar:
        is_rebalance = date in rebalances
        trade_info_by_strategy: dict[str, dict[str, Any]] = {}
        if is_rebalance:
            row = decision_map.get(date.strftime("%Y-%m-%d"), {})
            leader_codes = [code for code in LEADER_BASKET if code in leader_histories]
            targets = {
                "proxy_top5_always_in": split_codes(row.get("top5_codes", "")),
                threshold_strategy: split_codes(row.get("threshold_codes", "")),
                "leader_basket_equal": leader_codes,
            }
            for strategy, account in accounts.items():
                trade_info_by_strategy[strategy] = rebalance_account(account, targets[strategy], date, args)
        for strategy, account in accounts.items():
            value = account_value(account, date)
            info = trade_info_by_strategy.get(strategy, {})
            holding_names = [f"{code}{names.get(code, LEADER_BASKET.get(code, ''))}" for code in account.positions]
            records.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "strategy": strategy,
                    "equity": round(value, 2),
                    "cash": round(account.cash, 2),
                    "holding_count": len(account.positions),
                    "holdings": ",".join(holding_names),
                    "rebalance": is_rebalance,
                    "turnover": round(float(info.get("turnover", 0.0)), 6),
                    "trade_cost": round(float(info.get("trade_cost", 0.0)), 2),
                    "buy_value": round(float(info.get("buy_value", 0.0)), 2),
                    "sell_value": round(float(info.get("sell_value", 0.0)), 2),
                }
            )
    equity = pd.DataFrame(records)
    if "沪深300" in indexes:
        equity = pd.concat([equity, index_curve("CSI300_buy_hold", indexes["沪深300"], calendar, args.initial_capital)], ignore_index=True)
    return equity


def run_v2_streaming(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], dict[str, Any], datetime, datetime]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    universe, errors = load_market_universe(output_dir, args)
    end_dt = datetime.strptime(args.date, "%Y%m%d")
    analysis_start_dt = end_dt.replace(year=end_dt.year - args.years)
    fetch_start_dt = analysis_start_dt - pd.Timedelta(days=260)
    start_date = fetch_start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")
    indexes = load_indexes(args, start_date, end_date, cache_dir, errors)
    if "沪深300" not in indexes or indexes["沪深300"].empty:
        raise SystemExit("missing CSI300 history; cannot build stable V2 rebalance calendar")
    calendar = [
        date
        for date in indexes["沪深300"]["date"].tolist()
        if pd.Timestamp(analysis_start_dt) <= date <= pd.Timestamp(end_dt)
    ]
    rebalances = rebalance_dates(calendar, args.rebalance)
    rebalance_texts = [date.strftime("%Y-%m-%d") for date in rebalances]
    names = {str(row["code"]).zfill(6): safe_text(row["name"]) for _, row in universe.iterrows()}

    score_rows: list[dict[str, Any]] = []
    scanned_histories = 0
    total = len(names)
    started_at = time.time()
    worker_args = {
        "force_refresh": args.force_refresh,
        "timeout": args.timeout,
        "min_amount": args.min_amount,
        "min_price": args.min_price,
        "score_version": args.score_version,
    }
    tasks = [(code, name, start_date, end_date, str(cache_dir), worker_args, rebalance_texts) for code, name in names.items()]
    if args.workers and args.workers > 1:
        print(f"[score] parallel workers={args.workers} universe={total} rebalances={len(rebalances)}", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(score_history_task, task) for task in tasks]
            for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                code, name, rows, error, hist_len = future.result()
                if error:
                    errors.append(f"{code}: {error}")
                if hist_len >= 180:
                    scanned_histories += 1
                score_rows.extend(rows)
                if idx == 1 or idx % 100 == 0 or idx == total:
                    elapsed = time.time() - started_at
                    print(f"[score] {idx}/{total} scanned={scanned_histories} score_rows={len(score_rows)} elapsed={elapsed:.1f}s latest={code} {name}", flush=True)
    else:
        for idx, task in enumerate(tasks, start=1):
            code, name, rows, error, hist_len = score_history_task(task)
            if error:
                errors.append(f"{code}: {error}")
            if hist_len >= 180:
                scanned_histories += 1
            score_rows.extend(rows)
            if idx == 1 or idx % 100 == 0 or idx == total:
                elapsed = time.time() - started_at
                print(f"[score] {idx}/{total} scanned={scanned_histories} score_rows={len(score_rows)} elapsed={elapsed:.1f}s latest={code} {name}", flush=True)

    scores = pd.DataFrame(score_rows)
    if args.market_guard or args.sticky_hold:
        decisions, selected_codes = build_guarded_decisions_from_scores(scores, rebalances, indexes["沪深300"], args)
    else:
        decisions, selected_codes = build_decisions_from_scores(scores, rebalances, args)
    selected_histories = load_selected_histories(selected_codes, names, start_date, end_date, cache_dir, args, errors)
    leader_histories = load_selected_histories(set(LEADER_BASKET.keys()), {**names, **LEADER_BASKET}, start_date, end_date, cache_dir, args, errors)
    equity = run_capital_from_decisions(args, decisions, selected_histories, names, leader_histories, indexes, analysis_start_dt, end_dt)

    meta = {
        "run_date": args.date,
        "analysis_start_date": analysis_start_dt.strftime("%Y%m%d"),
        "fetch_start_date": start_date,
        "end_date": end_date,
        "years": args.years,
        "initial_capital": args.initial_capital,
        "universe_mode": "full_market_exchange_list_v2_streaming",
        "universe_codes": len(names),
        "loaded_histories": scanned_histories,
        "selected_histories": len(selected_histories),
        "leader_histories": len(leader_histories),
        "score_rows": len(scores),
        "top_n": args.top_n,
        "threshold": args.threshold,
        "hold_threshold": args.hold_threshold,
        "score_version": args.score_version,
        "market_guard": args.market_guard,
        "sticky_hold": args.sticky_hold,
        "max_new_per_rebalance": args.max_new_per_rebalance,
        "rebalance": args.rebalance,
        "commission_rate": args.commission_rate,
        "stamp_duty_rate": args.stamp_duty_rate,
        "max_codes": args.max_codes,
        "include_bse": args.include_bse,
        "workers": args.workers,
        "method_note": "V2 streaming mode uses the full-market exchange universe and scores each stock without keeping all historical DataFrames in memory. It removes current learning-pool universe bias; delisted coverage still depends on exchange/API availability.",
    }
    return equity, decisions, scores, yearly_metrics(equity, args.initial_capital), errors, meta, analysis_start_dt, end_dt


def load_v2_histories(args: argparse.Namespace) -> tuple[dict[str, pd.DataFrame], dict[str, str], dict[str, pd.DataFrame], dict[str, pd.DataFrame], list[str], dict[str, Any], datetime, datetime]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"
    universe, errors = load_market_universe(output_dir, args)
    end_dt = datetime.strptime(args.date, "%Y%m%d")
    analysis_start_dt = end_dt.replace(year=end_dt.year - args.years)
    fetch_start_dt = analysis_start_dt - pd.Timedelta(days=260)
    start_date = fetch_start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    histories: dict[str, pd.DataFrame] = {}
    names = {str(row["code"]).zfill(6): safe_text(row["name"]) for _, row in universe.iterrows()}
    total = len(names)
    started_at = time.time()
    if args.workers and args.workers > 1:
        worker_args = {"force_refresh": args.force_refresh, "timeout": args.timeout}
        tasks = [(code, name, start_date, end_date, str(cache_dir), worker_args) for code, name in names.items()]
        print(f"[history] parallel workers={args.workers} universe={total}", flush=True)
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(fetch_history_task, task) for task in tasks]
            for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                code, name, hist, error = future.result()
                if error:
                    errors.append(f"{code}: {error}")
                elif len(hist) < 180:
                    errors.append(f"{code}: history too short ({len(hist)})")
                else:
                    histories[code] = hist
                if idx == 1 or idx % 100 == 0 or idx == total:
                    elapsed = time.time() - started_at
                    print(f"[history] {idx}/{total} loaded={len(histories)} elapsed={elapsed:.1f}s latest={code} {name}", flush=True)
    else:
        for idx, (code, name) in enumerate(names.items(), start=1):
            if idx == 1 or idx % 100 == 0 or idx == total:
                loaded = len(histories)
                elapsed = time.time() - started_at
                print(f"[history] {idx}/{total} loaded={loaded} elapsed={elapsed:.1f}s latest={code} {name}", flush=True)
            hist, error = fetch_stock_history(code, start_date, end_date, cache_dir, args)
            if error:
                errors.append(f"{code}: {error}")
                continue
            if len(hist) < 180:
                errors.append(f"{code}: history too short ({len(hist)})")
                continue
            histories[code] = hist
            if args.sleep_sec > 0:
                time.sleep(args.sleep_sec)

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
        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

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
        "universe_mode": "full_market_exchange_list_v2",
        "universe_codes": len(names),
        "loaded_histories": len(histories),
        "leader_histories": len(leader_histories),
        "top_n": args.top_n,
        "threshold": args.threshold,
        "rebalance": args.rebalance,
        "commission_rate": args.commission_rate,
        "stamp_duty_rate": args.stamp_duty_rate,
        "max_codes": args.max_codes,
        "include_bse": args.include_bse,
        "workers": args.workers,
        "method_note": "V2 uses exchange/full-market code universe instead of the current learning pool. Future listings are naturally excluded by missing pre-listing history and 130-trading-day warmup. Delisted-name coverage depends on exchange list/API availability, so this is a major reduction of current-pool bias but not yet a perfect delisting-inclusive institutional backtest.",
    }
    return histories, names, leader_histories, indexes, errors, meta, analysis_start_dt, end_dt


def write_v2_report(path: Path, capital_summary: pd.DataFrame, yearly: pd.DataFrame, decisions: pd.DataFrame, errors: list[str], meta: dict[str, Any]) -> None:
    threshold_name = f"threshold_{int(meta['threshold'])}_cash_when_none"
    lines = [
        f"# 全市场资金账户回测 V2 - {datetime.strptime(meta['run_date'], '%Y%m%d'):%Y-%m-%d}",
        "",
        "定位：V2 不再用当前学习池回看历史，而是用全市场股票列表建立候选池，再在每个历史调仓点按当时已有行情、流动性、价格、趋势、不过热和风险代理分重新选股。",
        "",
        "## 回测设定",
        f"- 起始资金：{meta['initial_capital']:.0f} 元。",
        f"- 时间：{meta['analysis_start_date']} 至 {meta['end_date']}，约 {meta['years']} 年；另统计最近 1 年。",
        f"- 候选池：全市场交易所列表 {meta['universe_codes']} 只，扫描到有效历史行情 {meta['loaded_histories']} 只；账户曲线实际加载被选中股票 {meta.get('selected_histories', '-')} 只。",
        f"- 调仓：{'月度' if meta['rebalance'] == 'M' else '周度'}，每次最多持有 {meta['top_n']} 只。",
        f"- 评分版本：{meta.get('score_version', 'v1')}；买入门槛：代理分 >= {meta['threshold']:.0f}；持有门槛：代理分 >= {meta.get('hold_threshold', '-') }。",
        f"- 风控/换手：沪深300趋势风控 {'开启' if meta.get('market_guard') else '关闭'}；粘性持仓 {'开启' if meta.get('sticky_hold') else '关闭'}；单次最多新增 {meta.get('max_new_per_rebalance', '-')} 只。",
        f"- 成本：买卖佣金 {meta['commission_rate']:.4%}，卖出印花税 {meta['stamp_duty_rate']:.4%}。",
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
        for row in frame.sort_values(["_order", "strategy"]).itertuples(index=False):
            lines.append(
                f"| {row.period} | {row.strategy} | {row.start_value} | {row.end_value} | {row.normalized_end_value} | {row.total_return_pct}% | {row.annualized_return_pct}% | {row.max_drawdown_pct}% | {row.avg_holding_count} | {row.cash_day_rate_pct}% | {row.avg_turnover_on_rebalance_pct}% | {row.total_trade_cost} |"
            )

    lines += ["", "## 直接结论"]
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
        top5 = capital_summary[(capital_summary["period"] == f"full_{meta['years']}y") & (capital_summary["strategy"] == "proxy_top5_always_in")]
        if not top5.empty:
            row = top5.iloc[0]
            lines.append(f"- 强制 Top5 平均调仓换手 {row['avg_turnover_on_rebalance_pct']}%，用来判断全市场选股是否过度换手。")

    lines += [
        "",
        "## 年度拆分",
        "| 年份 | 策略 | 年初 | 年末 | 10万起算期末 | 收益 | 最大回撤 | 平均持股数 | 空仓天数占比 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if yearly.empty:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    else:
        for row in yearly.sort_values(["period", "strategy"]).itertuples(index=False):
            lines.append(
                f"| {row.period} | {row.strategy} | {row.start_value} | {row.end_value} | {row.normalized_end_value} | {row.total_return_pct}% | {row.max_drawdown_pct}% | {row.avg_holding_count} | {row.cash_day_rate_pct}% |"
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
        "## V2 与 V1 的区别",
        "- V1 用当前学习池前 50 只回看历史，容易把今天已经胜出的公司带回过去。",
        "- V2 用全市场交易所列表，每个历史调仓点从当时已有足够历史行情和流动性的股票里重新选，去掉当前学习池成分偏差。",
        "- V2 仍可能有退市股覆盖不足、停牌/涨跌停不可成交、滑点、整数手、真实 ST 状态缺失等限制，不能等同机构级逐笔回测。",
    ]
    if errors:
        lines += ["", "## 数据源提醒"]
        for error in errors[:80]:
            lines.append(f"- {error}")
        if len(errors) > 80:
            lines.append(f"- ... 其余 {len(errors) - 80} 条数据源提示见日志/缓存。")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    equity, decisions, scores, yearly, errors, meta, analysis_start_dt, end_dt = run_v2_streaming(args)
    capital_summary = summarize_capital(equity, args, analysis_start_dt, end_dt)

    date_key = args.date
    equity.to_csv(output_dir / f"market_v2_equity_curve_{date_key}.csv", index=False, encoding="utf-8-sig")
    decisions.to_csv(output_dir / f"market_v2_rebalance_decisions_{date_key}.csv", index=False, encoding="utf-8-sig")
    scores.to_csv(output_dir / f"market_v2_score_table_{date_key}.csv", index=False, encoding="utf-8-sig")
    capital_summary.to_csv(output_dir / f"market_v2_capital_summary_{date_key}.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(output_dir / f"market_v2_yearly_summary_{date_key}.csv", index=False, encoding="utf-8-sig")
    (output_dir / f"market_v2_backtest_meta_{date_key}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = report_dir / f"market_v2_backtest_{date_key}.md"
    write_v2_report(report_path, capital_summary, yearly, decisions, errors, meta)
    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "universe_codes": meta["universe_codes"],
                "loaded_histories": meta["loaded_histories"],
                "selected_histories": meta.get("selected_histories"),
                "score_rows": meta.get("score_rows"),
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
