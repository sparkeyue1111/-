#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import signal
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

INDEX_BENCHMARKS = {
    "沪深300": "000300",
    "中证500": "000905",
    "创业板指": "399006",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate AI stock selection quality, thresholds, and watchlist turnover.")
    parser.add_argument("--final-layer-dir", default="/app/data/final_layers")
    parser.add_argument("--learning-pool-dir", default="/app/data/learning_pool")
    parser.add_argument("--validation-dir", default="/app/data/strategy_validation")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--random-size", type=int, default=int(os.environ.get("VALIDATION_RANDOM_SIZE", "20")))
    parser.add_argument("--pool-size", type=int, default=int(os.environ.get("VALIDATION_POOL_SIZE", "50")))
    parser.add_argument("--stable-window", type=int, default=int(os.environ.get("STABLE_WINDOW", "5")))
    parser.add_argument("--stable-min-hits", type=int, default=int(os.environ.get("STABLE_MIN_HITS", "3")))
    parser.add_argument("--executable-final-threshold", type=float, default=float(os.environ.get("EXECUTABLE_FINAL_THRESHOLD", "80")))
    parser.add_argument("--executable-ai-threshold", type=float, default=float(os.environ.get("EXECUTABLE_AI_THRESHOLD", "50")))
    parser.add_argument("--thresholds", default=os.environ.get("VALIDATION_THRESHOLDS", "55,60,65,70,75,80"))
    parser.add_argument("--horizons", default=os.environ.get("VALIDATION_HORIZONS", "30,60,90"))
    parser.add_argument("--min-samples", type=int, default=int(os.environ.get("VALIDATION_MIN_SAMPLES", "30")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "35")))
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


def normalize_code(raw: Any) -> str:
    match = re.search(r"(\d{6})", safe_text(raw))
    return match.group(1) if match else ""


def is_main_a_share(code: str) -> bool:
    return bool(re.fullmatch(r"[036]\d{5}", code))


def parse_int_list(text: str) -> list[int]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    return values


def date_from_name(path: Path) -> datetime | None:
    match = re.search(r"(\d{8})", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str})


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for column in candidates:
        if column in df.columns:
            return column
    return None


def call_source(fn_name: str, timeout: int) -> tuple[pd.DataFrame, str]:
    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{fn_name} timeout after {timeout}s")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)
    try:
        df = getattr(ak, fn_name)()
        if isinstance(df, pd.DataFrame):
            return df, ""
        return pd.DataFrame(), f"{fn_name}: not dataframe"
    except Exception as exc:
        return pd.DataFrame(), f"{fn_name}: {type(exc).__name__}: {str(exc)[:240]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def load_spot(timeout: int) -> tuple[pd.DataFrame, list[str]]:
    errors = []
    for fn_name in ("stock_zh_a_spot", "stock_zh_a_spot_em"):
        raw, error = call_source(fn_name, timeout)
        if error:
            errors.append(error)
            continue
        if not raw.empty:
            return normalize_spot(raw), errors
        errors.append(f"{fn_name}: empty dataframe")
    return pd.DataFrame(), errors


def normalize_spot(raw: pd.DataFrame) -> pd.DataFrame:
    code_col = pick_column(raw, ["代码", "code", "股票代码"])
    name_col = pick_column(raw, ["名称", "name", "股票名称"])
    price_col = pick_column(raw, ["最新价", "最新", "现价", "trade", "close"])
    amount_col = pick_column(raw, ["成交额", "amount"])
    total_mv_col = pick_column(raw, ["总市值", "总市值-元", "总市值(元)"])
    if code_col is None or name_col is None or price_col is None:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["code"] = raw[code_col].map(normalize_code)
    out["name"] = raw[name_col].map(safe_text)
    out["price"] = pd.to_numeric(raw[price_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    if amount_col:
        out["amount"] = pd.to_numeric(raw[amount_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    else:
        out["amount"] = math.nan
    if total_mv_col:
        out["total_mv"] = pd.to_numeric(raw[total_mv_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    else:
        out["total_mv"] = math.nan
    out = out[out["code"].map(is_main_a_share)]
    out = out[~out["name"].str.contains("ST|退|N|C", case=False, regex=True, na=False)]
    out = out[pd.to_numeric(out["price"], errors="coerce") > 0]
    return out.drop_duplicates("code")


def load_index_quotes(timeout: int) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors = []
    raw, error = call_source("stock_zh_index_spot_em", timeout)
    quotes: dict[str, dict[str, Any]] = {}
    if error:
        errors.append(error)
    elif raw.empty:
        errors.append("stock_zh_index_spot_em: empty dataframe")
    else:
        code_col = pick_column(raw, ["代码", "code"])
        name_col = pick_column(raw, ["名称", "name"])
        price_col = pick_column(raw, ["最新价", "最新", "现价", "close"])
        if code_col is None or price_col is None:
            errors.append(f"index columns missing: {list(raw.columns)}")
        else:
            for _, row in raw.iterrows():
                code = normalize_code(row.get(code_col))
                for name, benchmark_code in INDEX_BENCHMARKS.items():
                    if code == benchmark_code:
                        quotes[name] = {
                            "code": benchmark_code,
                            "name": name,
                            "price": safe_float(row.get(price_col)),
                            "source_name": safe_text(row.get(name_col)) if name_col else name,
                            "source": "stock_zh_index_spot_em",
                        }

    for name, benchmark_code in INDEX_BENCHMARKS.items():
        if name in quotes:
            continue
        daily_symbol = ("sz" if benchmark_code.startswith("399") else "sh") + benchmark_code
        for fn_name, kwargs in [
            ("stock_zh_index_daily", {"symbol": daily_symbol}),
            ("index_zh_a_hist", {"symbol": benchmark_code, "period": "daily"}),
        ]:
            try:
                df = getattr(ak, fn_name)(**kwargs)
                if not isinstance(df, pd.DataFrame) or df.empty:
                    continue
                close_col = pick_column(df, ["close", "收盘", "收盘价"])
                if close_col is None:
                    continue
                price = safe_float(df[close_col].iloc[-1])
                if not math.isnan(price):
                    quotes[name] = {
                        "code": benchmark_code,
                        "name": name,
                        "price": price,
                        "source_name": daily_symbol,
                        "source": fn_name,
                    }
                    break
            except Exception as exc:
                errors.append(f"{fn_name} {benchmark_code}: {type(exc).__name__}: {str(exc)[:160]}")
    missing = [name for name in INDEX_BENCHMARKS if name not in quotes]
    if missing:
        errors.append(f"missing index quotes: {','.join(missing)}")
    return quotes, errors


def item_from_trade(row: dict[str, Any]) -> dict[str, Any]:
    price = safe_float(row.get("snapshot_price"))
    return {
        "code": str(row.get("code", "")).zfill(6),
        "name": safe_text(row.get("name")),
        "price": round(price, 4) if not math.isnan(price) else "",
        "final_research_score": safe_float(row.get("final_research_score"), 0.0),
        "ai_score": safe_float(row.get("ai_score"), 0.0),
        "plan_level": safe_text(row.get("plan_level")),
        "expectation_gap": safe_text(row.get("expectation_gap")),
        "valuation_level": safe_text(row.get("valuation_level")),
    }


def item_from_spot(row: pd.Series) -> dict[str, Any]:
    return {
        "code": str(row["code"]).zfill(6),
        "name": safe_text(row.get("name")),
        "price": round(safe_float(row.get("price")), 4),
        "amount": safe_float(row.get("amount")),
        "total_mv": safe_float(row.get("total_mv")),
    }


def latest_snapshot_paths(snapshot_dir: Path, date_key: str) -> list[Path]:
    paths = []
    for path in snapshot_dir.glob("strategy_snapshot_*.json"):
        path_dt = date_from_name(path)
        if path_dt is None or path.name.endswith(f"{date_key}.json"):
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.name)


def build_groups(trade_rows: list[dict[str, Any]], pool_df: pd.DataFrame, spot_df: pd.DataFrame, args: argparse.Namespace) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    thresholds = parse_int_list(args.thresholds)
    sorted_trades = sorted([item_from_trade(row) for row in trade_rows], key=lambda item: item["final_research_score"], reverse=True)
    buy_watch = [item for item in sorted_trades if item["plan_level"] == "买入前观察"]
    eligible = [item for item in sorted_trades if item["plan_level"] in {"买入前观察", "观察"}]

    groups: dict[str, list[dict[str, Any]]] = {
        "ai_top5": sorted_trades[:5],
        "ai_eligible": eligible,
        "ai_buy_watch": buy_watch,
    }
    for threshold in thresholds:
        groups[f"threshold_{threshold}"] = [
            item for item in sorted_trades if item["final_research_score"] >= threshold and item["plan_level"] != "降级"
        ]

    if not pool_df.empty and "code" in pool_df.columns:
        pool = pool_df.copy()
        pool["code"] = pool["code"].map(lambda value: normalize_code(value).zfill(6))
        if "rank" in pool.columns:
            pool = pool.sort_values("rank")
        elif "score" in pool.columns:
            pool = pool.sort_values("score", ascending=False)
        groups["pool_top50"] = [
            {
                "code": str(row["code"]).zfill(6),
                "name": safe_text(row.get("name")),
                "price": safe_float(row.get("price")),
                "pool_score": safe_float(row.get("score")),
            }
            for _, row in pool.head(args.pool_size).iterrows()
        ]
    else:
        groups["pool_top50"] = []

    liquid = spot_df.copy()
    if "price" not in liquid.columns or "code" not in liquid.columns:
        liquid = pd.DataFrame(columns=["code", "name", "price", "amount", "total_mv"])
    if "amount" in liquid.columns:
        liquid = liquid[pd.to_numeric(liquid["amount"], errors="coerce") >= 100_000_000]
    liquid = liquid[pd.to_numeric(liquid["price"], errors="coerce") >= 3]
    sample_size = max(args.random_size, len(groups["ai_top5"]), 5)
    if len(liquid) >= sample_size:
        sampled = liquid.sample(n=sample_size, random_state=int(args.date)).sort_values("code")
    else:
        sampled = liquid
    groups["random_liquid"] = [item_from_spot(row) for _, row in sampled.iterrows()]

    leader_rows = []
    spot_by_code = {str(row["code"]).zfill(6): row for _, row in spot_df.iterrows()} if not spot_df.empty else {}
    for code, name in LEADER_BASKET.items():
        row = spot_by_code.get(code)
        if row is not None:
            leader_rows.append(item_from_spot(row))
        else:
            leader_rows.append({"code": code, "name": name, "price": "", "missing": True})
    groups["industry_leaders"] = leader_rows

    metadata = {
        "thresholds": thresholds,
        "random_sample_size": len(groups["random_liquid"]),
        "leader_basket_size": len(groups["industry_leaders"]),
    }
    return groups, metadata


def current_price_lookup(spot_df: pd.DataFrame, trade_rows: list[dict[str, Any]]) -> dict[str, float]:
    lookup: dict[str, float] = {}
    if not spot_df.empty:
        for _, row in spot_df.iterrows():
            code = str(row["code"]).zfill(6)
            price = safe_float(row.get("price"))
            if not math.isnan(price):
                lookup[code] = price
    for row in trade_rows:
        code = str(row.get("code", "")).zfill(6)
        price = safe_float(row.get("snapshot_price"))
        if code and not math.isnan(price):
            lookup.setdefault(code, price)
    return lookup


def group_return(items: list[dict[str, Any]], price_lookup: dict[str, float]) -> dict[str, Any]:
    returns = []
    missing = 0
    for item in items:
        code = str(item.get("code", "")).zfill(6)
        start_price = safe_float(item.get("price"))
        end_price = price_lookup.get(code, math.nan)
        if math.isnan(start_price) or start_price <= 0 or math.isnan(end_price) or end_price <= 0:
            missing += 1
            continue
        returns.append((end_price / start_price - 1) * 100)
    if not returns:
        return {"n": 0, "missing": missing, "avg_return": math.nan, "median_return": math.nan, "win_rate": math.nan}
    return {
        "n": len(returns),
        "missing": missing,
        "avg_return": sum(returns) / len(returns),
        "median_return": median(returns),
        "win_rate": sum(1 for value in returns if value > 0) / len(returns) * 100,
    }


def index_return(snapshot: dict[str, Any], current_index_quotes: dict[str, dict[str, Any]], name: str) -> float:
    start = safe_float(((snapshot.get("index_benchmarks") or {}).get(name) or {}).get("price"))
    end = safe_float((current_index_quotes.get(name) or {}).get("price"))
    if math.isnan(start) or start <= 0 or math.isnan(end) or end <= 0:
        return math.nan
    return (end / start - 1) * 100


def evaluate_matured_snapshots(
    snapshot_dir: Path,
    date_key: str,
    horizons: list[int],
    spot_df: pd.DataFrame,
    trade_rows: list[dict[str, Any]],
    current_index_quotes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    current_dt = datetime.strptime(date_key, "%Y%m%d")
    price_lookup = current_price_lookup(spot_df, trade_rows)
    rows = []
    for path in latest_snapshot_paths(snapshot_dir, date_key):
        snapshot_dt = date_from_name(path)
        if snapshot_dt is None:
            continue
        age_days = (current_dt - snapshot_dt).days
        snapshot = read_json(path) or {}
        groups = snapshot.get("groups") or {}
        for horizon in horizons:
            if age_days < horizon or age_days > horizon + 7:
                continue
            csi300 = index_return(snapshot, current_index_quotes, "沪深300")
            for group_name in ["ai_top5", "ai_eligible", "ai_buy_watch", "random_liquid", "industry_leaders"] + [
                key for key in groups if key.startswith("threshold_")
            ]:
                items = groups.get(group_name) or []
                stats = group_return(items, price_lookup)
                avg_return = stats["avg_return"]
                excess = avg_return - csi300 if not math.isnan(avg_return) and not math.isnan(csi300) else math.nan
                rows.append(
                    {
                        "snapshot_date": snapshot.get("date", path.name),
                        "evaluation_date": date_key,
                        "age_days": age_days,
                        "horizon_days": horizon,
                        "group": group_name,
                        "n": stats["n"],
                        "missing": stats["missing"],
                        "avg_return": round(avg_return, 4) if not math.isnan(avg_return) else "",
                        "median_return": round(stats["median_return"], 4) if not math.isnan(stats["median_return"]) else "",
                        "win_rate": round(stats["win_rate"], 2) if not math.isnan(stats["win_rate"]) else "",
                        "csi300_return": round(csi300, 4) if not math.isnan(csi300) else "",
                        "excess_vs_csi300": round(excess, 4) if not math.isnan(excess) else "",
                    }
                )
    return rows


def compute_churn(current_groups: dict[str, list[dict[str, Any]]], previous_snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    previous_groups = (previous_snapshot or {}).get("groups") or {}
    for group_name in ["ai_top5", "ai_eligible", "pool_top50"]:
        current_codes = {str(item.get("code", "")).zfill(6) for item in current_groups.get(group_name, []) if item.get("code")}
        previous_codes = {str(item.get("code", "")).zfill(6) for item in previous_groups.get(group_name, []) if item.get("code")}
        if not previous_codes:
            rows.append(
                {
                    "group": group_name,
                    "current_count": len(current_codes),
                    "previous_count": len(previous_codes),
                    "overlap": 0,
                    "turnover_rate": "",
                    "added": ",".join(sorted(current_codes)),
                    "removed": "",
                    "status": "baseline",
                }
            )
            continue
        overlap = len(current_codes & previous_codes)
        base = max(len(current_codes), len(previous_codes), 1)
        turnover = 1 - overlap / base
        status = "ok"
        if group_name == "ai_top5" and turnover > 0.6:
            status = "high_turnover"
        elif group_name in {"ai_eligible", "pool_top50"} and turnover > 0.35:
            status = "high_turnover"
        rows.append(
            {
                "group": group_name,
                "current_count": len(current_codes),
                "previous_count": len(previous_codes),
                "overlap": overlap,
                "turnover_rate": round(turnover, 4),
                "added": ",".join(sorted(current_codes - previous_codes)),
                "removed": ",".join(sorted(previous_codes - current_codes)),
                "status": status,
            }
        )
    return rows


def appearance_counts(snapshot_paths: list[Path], group_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in snapshot_paths:
        snapshot = read_json(path) or {}
        items = ((snapshot.get("groups") or {}).get(group_name) or [])
        for item in items:
            code = str(item.get("code", "")).zfill(6)
            if code:
                counts[code] = counts.get(code, 0) + 1
    return counts


def executable_candidates(
    current_groups: dict[str, list[dict[str, Any]]],
    recent_paths: list[Path],
    stable_window: int,
    stable_min_hits: int,
    final_threshold: float,
    ai_threshold: float,
) -> list[dict[str, Any]]:
    recent = recent_paths[-stable_window:]
    counts = appearance_counts(recent, "ai_eligible")
    for item in current_groups.get("ai_eligible", []):
        code = str(item.get("code", "")).zfill(6)
        if code:
            counts[code] = counts.get(code, 0) + 1
    rows = []
    for item in current_groups.get("ai_eligible", []):
        code = str(item.get("code", "")).zfill(6)
        score = safe_float(item.get("final_research_score"))
        ai_score = safe_float(item.get("ai_score"))
        hits = counts.get(code, 0)
        pass_gate = score >= final_threshold and ai_score >= ai_threshold and hits >= stable_min_hits and item.get("plan_level") != "降级"
        rows.append(
            {
                **item,
                "stable_hits": hits,
                "stable_window": stable_window,
                "executable_gate": "pass" if pass_gate else "wait",
                "gate_reason": "通过稳定性门槛" if pass_gate else f"需要 final>={final_threshold:.0f}、AI>={ai_threshold:.0f}、近{stable_window}次入选>= {stable_min_hits} 次",
            }
        )
    return rows


def threshold_calibration(validation_dir: Path, min_samples: int) -> list[dict[str, Any]]:
    files = sorted(validation_dir.glob("validation_results_*.csv"))
    frames = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype={"snapshot_date": str, "evaluation_date": str})
        except pd.errors.EmptyDataError:
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return []
    all_results = pd.concat(frames, ignore_index=True)
    rows = []
    groups = [group for group in sorted(all_results["group"].dropna().unique()) if str(group).startswith("threshold_")]
    for group in groups:
        subset = all_results[all_results["group"] == group].copy()
        subset["excess_vs_csi300"] = pd.to_numeric(subset["excess_vs_csi300"], errors="coerce")
        subset["avg_return"] = pd.to_numeric(subset["avg_return"], errors="coerce")
        valid = subset.dropna(subset=["excess_vs_csi300"])
        samples = len(valid)
        if samples:
            avg_excess = valid["excess_vs_csi300"].mean()
            positive_rate = (valid["excess_vs_csi300"] > 0).mean() * 100
        else:
            avg_excess = math.nan
            positive_rate = math.nan
        rows.append(
            {
                "threshold_group": group,
                "samples": samples,
                "avg_excess_vs_csi300": round(avg_excess, 4) if not math.isnan(avg_excess) else "",
                "positive_excess_rate": round(positive_rate, 2) if not math.isnan(positive_rate) else "",
                "decision": "样本不足，不调整阈值" if samples < min_samples else ("可考虑保留/提高权重" if avg_excess > 0 else "阈值无效，需下调策略权重"),
            }
        )
    return rows


def consecutive_empty_days(snapshot_dir: Path, date_key: str) -> int:
    paths = latest_snapshot_paths(snapshot_dir, date_key) + [snapshot_dir / f"strategy_snapshot_{date_key}.json"]
    streak = 0
    for path in reversed(paths):
        snapshot = read_json(path) or {}
        rows = snapshot.get("executable_candidates") or []
        passed = [row for row in rows if row.get("executable_gate") == "pass"]
        if passed:
            break
        streak += 1
    return streak


def write_report(
    path: Path,
    snapshot: dict[str, Any],
    validation_rows: list[dict[str, Any]],
    threshold_rows: list[dict[str, Any]],
    churn_rows: list[dict[str, Any]],
    executable_rows: list[dict[str, Any]],
    errors: list[str],
    args: argparse.Namespace,
) -> None:
    passed = [row for row in executable_rows if row.get("executable_gate") == "pass"]
    mature_rows = [row for row in validation_rows if row.get("group") in {"ai_top5", "random_liquid", "industry_leaders"}]
    high_turnover = [row for row in churn_rows if row.get("status") == "high_turnover"]
    dry_spell = snapshot.get("dry_spell_days", 0)

    lines = [
        f"# 策略验证层 V1 - {datetime.strptime(snapshot['date'], '%Y%m%d'):%Y-%m-%d}",
        "",
        "定位：验证 AI 选股是否跑赢随机/指数/行业龙头，校准买入阈值，并监控关注池是否高换手。该层不生成买卖指令，只决定策略证据是否足够。",
        "",
        "## 今日结论",
    ]
    if mature_rows:
        lines.append("- 已有到期样本，见下方“AI 对照验证”。")
    else:
        lines.append("- 暂无 30/60/90 天到期样本，今天继续建立验证账本；不能声称 AI 已被证明有效。")
    if threshold_rows:
        ready = [row for row in threshold_rows if row["samples"] >= args.min_samples]
        if ready:
            lines.append("- 阈值已有部分样本，可开始评估是否调整。")
        else:
            lines.append(f"- 阈值样本不足，至少需要每个阈值 {args.min_samples} 个到期样本；当前不调低门槛。")
    else:
        lines.append(f"- 阈值校准账本已建立，但还没有到期样本；当前不调低门槛。")
    if high_turnover:
        lines.append("- 关注池换手偏高，买入前必须等待稳定性门槛。")
    else:
        lines.append("- 当前没有检测到可判定的高换手；若是首日运行，则只是基线。")
    if passed:
        lines.append(f"- 今日稳定性门槛通过 {len(passed)} 只，可进入人工复核。")
    else:
        lines.append(f"- 今日没有通过稳定性门槛的可执行候选；连续等待 {dry_spell} 个样本日。等待本身是策略状态。")

    lines += [
        "",
        "## 1. AI 对照验证",
        "| 快照日 | 周期 | 组别 | 数量 | 平均收益 | 中位收益 | 胜率 | 沪深300 | 超额 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    if validation_rows:
        for row in validation_rows[:80]:
            lines.append(
                f"| {row['snapshot_date']} | {row['horizon_days']} | {row['group']} | {row['n']} | {row['avg_return']} | {row['median_return']} | {row['win_rate']} | {row['csi300_return']} | {row['excess_vs_csi300']} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - |")
        lines.append("")
        lines.append("暂无到期样本。30/60/90 天后会自动把 AI Top5、随机流动性组合、行业龙头篮子和指数放在同一张表里比较。")

    lines += [
        "",
        "## 2. 阈值校准",
        "| 阈值组 | 到期样本 | 平均超额 | 超额为正比例 | 决策 |",
        "|---|---:|---:|---:|---|",
    ]
    if threshold_rows:
        for row in threshold_rows:
            lines.append(
                f"| {row['threshold_group']} | {row['samples']} | {row['avg_excess_vs_csi300']} | {row['positive_excess_rate']} | {row['decision']} |"
            )
    else:
        lines.append("| - | 0 | - | - | 样本不足，不调整阈值 |")

    lines += [
        "",
        "## 3. 关注池换手监控",
        "| 组别 | 当前数量 | 上期数量 | 重合 | 换手率 | 状态 | 新增 | 移出 |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in churn_rows:
        lines.append(
            f"| {row['group']} | {row['current_count']} | {row['previous_count']} | {row['overlap']} | {row['turnover_rate']} | {row['status']} | {row['added']} | {row['removed']} |"
        )

    lines += [
        "",
        "## 稳定性门槛候选",
        f"规则：final>={args.executable_final_threshold:.0f}、AI>={args.executable_ai_threshold:.0f}、近 {args.stable_window} 次进入观察/买入前观察至少 {args.stable_min_hits} 次，且没有降级。",
        "",
        "| 代码 | 名称 | 最终分 | AI | 层级 | 稳定命中 | 结果 | 原因 |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    if executable_rows:
        for row in executable_rows:
            lines.append(
                f"| {row['code']} | {row['name']} | {row['final_research_score']:.2f} | {row['ai_score']:.2f} | {row['plan_level']} | {row['stable_hits']} | {row['executable_gate']} | {row['gate_reason']} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | wait | 今日没有观察候选 |")

    lines += [
        "",
        "## 使用原则",
        "- 在 AI 对照验证没有足够样本前，不把任何阈值当成永久有效。",
        "- 如果随机组合或行业龙头长期跑赢 AI Top5，说明选股层需要降权或重构。",
        "- 如果关注池高换手，宁可等待稳定性确认，也不每天追新增名单。",
        "- 如果连续多月没有通过门槛的候选，说明市场没有给系统舒服的机会，不应强行买入。",
    ]
    if errors:
        lines += ["", "## 数据源提醒"]
        for error in errors[:20]:
            lines.append(f"- {error}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    date_key = args.date
    validation_dir = Path(args.validation_dir)
    snapshot_dir = validation_dir / "snapshots"
    report_dir = Path(args.report_dir)
    validation_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    trade_path = Path(args.final_layer_dir) / f"final_trade_plan_{date_key}.json"
    trade_rows = read_json(trade_path) or read_json(Path(args.final_layer_dir) / "current_final_trade_plan.json") or []
    if not trade_rows:
        raise SystemExit("missing final trade plan; run final layers first")
    pool_df = load_csv(Path(args.learning_pool_dir) / "current_learning_pool.csv")
    if not pool_df.empty and "code" in pool_df.columns:
        pool_df["code"] = pool_df["code"].map(lambda value: normalize_code(value).zfill(6))

    errors: list[str] = []
    spot_df, spot_errors = load_spot(args.timeout)
    errors.extend(spot_errors)
    if not spot_df.empty:
        spot_df.to_csv(validation_dir / f"spot_cache_{date_key}.csv", index=False, encoding="utf-8-sig")
    else:
        cache_files = sorted(validation_dir.glob("spot_cache_*.csv"))
        if cache_files:
            try:
                spot_df = pd.read_csv(cache_files[-1], dtype={"code": str})
                errors.append(f"全市场行情源失败，随机基准使用缓存: {cache_files[-1].name}")
            except Exception as exc:
                errors.append(f"读取行情缓存失败: {type(exc).__name__}: {str(exc)[:160]}")
        if spot_df.empty and not pool_df.empty:
            fallback_cols = [column for column in ["code", "name", "price", "amount", "total_mv"] if column in pool_df.columns]
            spot_df = pool_df[fallback_cols].copy()
            errors.append("全市场行情源和缓存均不可用，随机基准临时降级为学习池内随机；该结果不能代表全市场随机。")
    index_quotes, index_errors = load_index_quotes(args.timeout)
    errors.extend(index_errors)

    groups, metadata = build_groups(trade_rows, pool_df, spot_df, args)
    previous_paths = latest_snapshot_paths(snapshot_dir, date_key)
    previous_snapshot = read_json(previous_paths[-1]) if previous_paths else None
    recent_paths = previous_paths
    executable_rows = executable_candidates(
        groups,
        recent_paths,
        args.stable_window,
        args.stable_min_hits,
        args.executable_final_threshold,
        args.executable_ai_threshold,
    )
    churn_rows = compute_churn(groups, previous_snapshot)

    snapshot = {
        "date": date_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata,
        "groups": groups,
        "index_benchmarks": index_quotes,
        "churn": churn_rows,
        "executable_candidates": executable_rows,
    }
    snapshot_path = snapshot_dir / f"strategy_snapshot_{date_key}.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (validation_dir / "current_strategy_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    validation_rows = evaluate_matured_snapshots(
        snapshot_dir,
        date_key,
        parse_int_list(args.horizons),
        spot_df,
        trade_rows,
        index_quotes,
    )
    pd.DataFrame(validation_rows).to_csv(validation_dir / f"validation_results_{date_key}.csv", index=False, encoding="utf-8-sig")

    threshold_rows = threshold_calibration(validation_dir, args.min_samples)
    pd.DataFrame(threshold_rows).to_csv(validation_dir / f"threshold_calibration_{date_key}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(churn_rows).to_csv(validation_dir / f"churn_{date_key}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(executable_rows).to_csv(validation_dir / f"executable_candidates_{date_key}.csv", index=False, encoding="utf-8-sig")
    (validation_dir / f"executable_candidates_{date_key}.json").write_text(
        json.dumps(executable_rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    snapshot["dry_spell_days"] = consecutive_empty_days(snapshot_dir, date_key)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (validation_dir / "current_strategy_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = report_dir / f"strategy_validation_{date_key}.md"
    write_report(report_path, snapshot, validation_rows, threshold_rows, churn_rows, executable_rows, errors, args)
    print(
        json.dumps(
            {
                "ok": True,
                "date": date_key,
                "validation_rows": len(validation_rows),
                "threshold_rows": len(threshold_rows),
                "churn_rows": len(churn_rows),
                "executable_pass": sum(1 for row in executable_rows if row.get("executable_gate") == "pass"),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
