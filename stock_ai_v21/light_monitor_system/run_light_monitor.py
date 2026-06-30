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
import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pre-market/intraday lightweight risk scan without full AI analysis.")
    parser.add_argument("--mode", choices=["premarket", "intraday"], required=True)
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--candidate-dir", default="/app/data/fundamental_first")
    parser.add_argument("--portfolio-dir", default="/app/data/paper_portfolio")
    parser.add_argument("--output-dir", default="/app/data/light_monitor")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--max-watch", type=int, default=20)
    parser.add_argument("--drop-alert-pct", type=float, default=-5.0)
    parser.add_argument("--overheat-pct", type=float, default=6.0)
    parser.add_argument("--near-stop-buffer", type=float, default=0.03)
    parser.add_argument("--volume-ratio-alert", type=float, default=3.0)
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


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 4:
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str})


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def pick_column(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def spot_map_from_frame(df: pd.DataFrame, source: str) -> tuple[dict[str, dict[str, Any]], str]:
    if df is None or df.empty:
        return {}, "empty dataframe"
    code_col = pick_column(df, ["代码", "code", "股票代码"])
    name_col = pick_column(df, ["名称", "name", "股票名称"])
    price_col = pick_column(df, ["最新价", "最新", "现价", "price", "close", "trade"])
    pct_col = pick_column(df, ["涨跌幅", "涨幅", "pct_chg", "changepercent"])
    amount_col = pick_column(df, ["成交额", "amount"])
    vr_col = pick_column(df, ["量比", "volume_ratio"])
    turnover_col = pick_column(df, ["换手率", "turnover", "turnover_rate"])
    if not code_col:
        return {}, f"code column missing: {list(df.columns)[:20]}"
    out: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = normalize_code(row.get(code_col))
        if not code:
            continue
        out[code] = {
            "name": safe_text(row.get(name_col)) if name_col else "",
            "price": safe_float(row.get(price_col)) if price_col else math.nan,
            "pct_chg": safe_float(row.get(pct_col)) if pct_col else math.nan,
            "amount": safe_float(row.get(amount_col)) if amount_col else math.nan,
            "volume_ratio": safe_float(row.get(vr_col)) if vr_col else math.nan,
            "turnover_rate": safe_float(row.get(turnover_col)) if turnover_col else math.nan,
        }
    return out, ""


def fetch_akshare_spot_map() -> tuple[dict[str, dict[str, Any]], str, str]:
    source = "akshare.stock_zh_a_spot_em"
    try:
        df = ak.stock_zh_a_spot_em()
        out, error = spot_map_from_frame(df, source)
        return out, source, error
    except Exception as exc:
        return {}, source, f"{type(exc).__name__}: {str(exc)[:200]}"


def fetch_efinance_spot_map() -> tuple[dict[str, dict[str, Any]], str, str]:
    source = "efinance.stock.get_realtime_quotes"
    try:
        import efinance as ef

        df = ef.stock.get_realtime_quotes()
        out, error = spot_map_from_frame(df, source)
        return out, source, error
    except Exception as exc:
        return {}, source, f"{type(exc).__name__}: {str(exc)[:200]}"


def tencent_symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def fetch_tencent_spot_map(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str, str]:
    source = "tencent.qt.gtimg.cn"
    clean_codes = sorted({normalize_code(code) for code in codes if normalize_code(code)})
    if not clean_codes:
        return {}, source, "no codes"
    symbols = ",".join(tencent_symbol(code) for code in clean_codes)
    url = f"http://qt.gtimg.cn/q={symbols}"
    try:
        response = requests.get(
            url,
            headers={"Referer": "http://finance.qq.com", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.encoding = "gbk"
        if response.status_code != 200:
            return {}, source, f"HTTP {response.status_code}"
        out: dict[str, dict[str, Any]] = {}
        for record in response.text.strip().split(";"):
            if not record.strip():
                continue
            data_start = record.find(chr(34))
            data_end = record.rfind(chr(34))
            if data_start == -1 or data_end <= data_start:
                continue
            fields = record[data_start + 1 : data_end].split("~")
            if len(fields) < 40:
                continue
            code = normalize_code(fields[2] if len(fields) > 2 else "")
            if not code:
                continue
            out[code] = {
                "name": safe_text(fields[1] if len(fields) > 1 else ""),
                "price": safe_float(fields[3] if len(fields) > 3 else math.nan),
                "pct_chg": safe_float(fields[32] if len(fields) > 32 else math.nan),
                "amount": safe_float(fields[37] if len(fields) > 37 else math.nan) * 10000,
                "volume_ratio": safe_float(fields[49] if len(fields) > 49 else math.nan),
                "turnover_rate": safe_float(fields[38] if len(fields) > 38 else math.nan),
            }
        if not out:
            return {}, source, "empty parsed payload"
        return out, source, ""
    except Exception as exc:
        return {}, source, f"{type(exc).__name__}: {str(exc)[:200]}"


def fetch_spot_map(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str, str]:
    errors: list[str] = []
    fetchers = [lambda: fetch_tencent_spot_map(codes), fetch_akshare_spot_map, fetch_efinance_spot_map]
    for fetcher in fetchers:
        out, source, error = fetcher()
        if out:
            if errors:
                return out, f"{source} (fallback)", "; ".join(errors)
            return out, source, ""
        message = error if error else "empty"
        errors.append(f"{source}: {message}")
    return {}, "none", "; ".join(errors)


def candidate_rows(candidate_df: pd.DataFrame, state: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    if not candidate_df.empty:
        for _, row in candidate_df.iterrows():
            code = normalize_code(row.get("code"))
            if code:
                by_code[code] = row.to_dict()

    selected: dict[str, dict[str, Any]] = {}

    def add(code: str, source: str, fallback: dict[str, Any] | None = None) -> None:
        code = normalize_code(code)
        if not code:
            return
        row = dict(by_code.get(code, fallback or {}))
        row["code"] = code
        row["monitor_source"] = source if not row.get("monitor_source") else f"{row.get('monitor_source')},{source}"
        selected[code] = row

    for pos in state.get("positions", []) or []:
        add(pos.get("code"), "position", pos)
    for order in state.get("pending_orders", []) or []:
        add(order.get("code"), "pending_order", order)

    if not candidate_df.empty:
        priority = candidate_df.copy()
        priority["_score"] = pd.to_numeric(priority.get("fundamental_first_score"), errors="coerce").fillna(0)
        priority["_trade"] = pd.to_numeric(priority.get("trade_score"), errors="coerce").fillna(0)
        priority = priority.sort_values(["_score", "_trade"], ascending=False)
        decisions = {"BUY_READY", "TRADE_CANDIDATE", "WATCH"}
        for _, row in priority.iterrows():
            if safe_text(row.get("decision")) in decisions:
                add(row.get("code"), f"decision:{safe_text(row.get('decision'))}", row.to_dict())
            if len(selected) >= args.max_watch:
                break
    return list(selected.values())[: args.max_watch]


def build_alert(row: dict[str, Any], spot: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    code = normalize_code(row.get("code"))
    name = safe_text(row.get("name")) or safe_text(spot.get("name")) or code
    decision = safe_text(row.get("decision"))
    source = safe_text(row.get("monitor_source"))
    price = safe_float(spot.get("price"), safe_float(row.get("current_price"), safe_float(row.get("signal_price"))))
    pct = safe_float(spot.get("pct_chg"))
    volume_ratio = safe_float(spot.get("volume_ratio"))
    trade_score = safe_float(row.get("trade_score"), safe_float(row.get("last_trade_score")))
    risk_stop = safe_float(row.get("risk_stop"))
    if math.isnan(risk_stop) and not math.isnan(price):
        risk_stop = price * 0.88

    flags: list[str] = []
    severity = "ok"
    action = "继续观察"

    is_position = "position" in source
    is_pending = "pending_order" in source

    if is_position and not math.isnan(price) and not math.isnan(risk_stop) and price <= risk_stop:
        flags.append("触发风控价")
        severity = "critical"
        action = "盘中复核卖出/减仓条件"
    elif is_position and not math.isnan(price) and not math.isnan(risk_stop) and price <= risk_stop * (1 + args.near_stop_buffer):
        flags.append("接近风控价")
        severity = "warning"
        action = "不加仓，准备降级复核"

    if args.mode == "intraday":
        if not math.isnan(pct) and pct <= args.drop_alert_pct:
            flags.append(f"盘中跌幅{pct:.2f}%")
            severity = "critical" if severity == "ok" else severity
            action = "检查是否有公告/行业风险，禁止机械补仓"
        if not math.isnan(pct) and pct >= args.overheat_pct:
            flags.append(f"盘中涨幅{pct:.2f}%偏热")
            if severity == "ok":
                severity = "warning"
            action = "不追高，等待盘后确认"
        if not math.isnan(volume_ratio) and volume_ratio >= args.volume_ratio_alert:
            flags.append(f"量比{volume_ratio:.2f}异常")
            if severity == "ok":
                severity = "warning"

    if is_pending:
        if decision == "BUY_READY":
            flags.append("待买信号仍在")
            if severity == "ok":
                severity = "info"
            action = "等待收盘后确认，避免盘中冲动成交"
        else:
            flags.append("待买信号降级")
            severity = "warning" if severity == "ok" else severity
            action = "取消或延后待买计划"

    if decision == "BUY_READY" and not is_pending:
        flags.append("严格买入候选")
        if severity == "ok":
            severity = "info"
    if not math.isnan(trade_score) and trade_score < 60 and decision in {"BUY_READY", "TRADE_CANDIDATE", "WATCH"}:
        flags.append(f"交易分偏弱{trade_score:.1f}")
        if severity == "ok":
            severity = "warning"

    if not flags:
        flags.append("无硬风控触发")

    return {
        "date": args.date,
        "mode": args.mode,
        "code": code,
        "name": name,
        "source": source,
        "decision": decision,
        "severity": severity,
        "action": action,
        "price": None if math.isnan(price) else round(price, 3),
        "pct_chg": None if math.isnan(pct) else round(pct, 2),
        "volume_ratio": None if math.isnan(volume_ratio) else round(volume_ratio, 2),
        "risk_stop": None if math.isnan(risk_stop) else round(risk_stop, 3),
        "trade_score": None if math.isnan(trade_score) else round(trade_score, 2),
        "flags": "；".join(flags),
    }


def write_report(alerts: list[dict[str, Any]], meta: dict[str, Any], args: argparse.Namespace) -> Path:
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"light_monitor_{args.mode}_{args.date}.md"
    title = "盘前风险扫描" if args.mode == "premarket" else "盘中轻量监控"
    lines = [
        f"# {title} {args.date}",
        "",
        "定位：这是中线系统的轻量雷达，只做风险/异动提示，不替代盘后完整 AI 分析。",
        "",
        f"- 监控数量：{len(alerts)}",
        f"- 行情源：{meta.get('spot_source')}",
        f"- 行情错误：{meta.get('spot_error') or '无'}",
        "",
        "| 严重度 | 股票 | 决策层 | 价格 | 涨跌幅 | 风控价 | 提示 | 动作 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    for row in sorted(alerts, key=lambda item: (order.get(item["severity"], 9), item["code"])):
        lines.append(
            f"| {row['severity']} | {row['name']}({row['code']}) | {row.get('decision') or '-'} | "
            f"{row.get('price') if row.get('price') is not None else '-'} | "
            f"{row.get('pct_chg') if row.get('pct_chg') is not None else '-'} | "
            f"{row.get('risk_stop') if row.get('risk_stop') is not None else '-'} | "
            f"{row.get('flags') or '-'} | {row.get('action') or '-'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_df = read_csv(Path(args.candidate_dir) / "current_fundamental_first_candidates.csv")
    state = read_json(Path(args.portfolio_dir) / "paper_portfolio_state.json", {})
    rows = candidate_rows(candidate_df, state, args)
    spot_map, spot_source, spot_error = fetch_spot_map([normalize_code(row.get("code")) for row in rows])
    alerts = [build_alert(row, spot_map.get(normalize_code(row.get("code")), {}), args) for row in rows]
    meta = {
        "ok": True,
        "date": args.date,
        "mode": args.mode,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "watch_count": len(rows),
        "alert_count": sum(1 for row in alerts if row["severity"] in {"critical", "warning"}),
        "critical_count": sum(1 for row in alerts if row["severity"] == "critical"),
        "spot_source": spot_source,
        "spot_error": spot_error,
    }
    report = write_report(alerts, meta, args)
    meta["report"] = str(report)

    df = pd.DataFrame(alerts)
    csv_path = output_dir / f"light_monitor_{args.mode}_{args.date}.csv"
    json_path = output_dir / f"light_monitor_{args.mode}_{args.date}.json"
    current_csv = output_dir / f"current_{args.mode}_light_monitor.csv"
    current_json = output_dir / f"current_{args.mode}_light_monitor.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_csv(current_csv, index=False, encoding="utf-8-sig")
    payload = {"meta": meta, "alerts": alerts}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    current_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    args = parse_args()
    meta = run(args)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
