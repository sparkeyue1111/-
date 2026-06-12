#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


DOC_CATEGORIES = {
    "periodic_report": r"年报|年度报告|半年报|半年度报告|一季报|三季报|季度报告",
    "regulatory_inquiry": r"问询函|关注函|监管函|回复|说明函|监管工作函|年报问询",
    "project_order": r"中标|合同|订单|项目|投资|扩产|产能|认证|客户|供应商|战略合作",
    "investor_relation": r"投资者关系|调研|业绩说明会|路演",
    "risk_warning": r"立案|处罚|违规|违法|风险提示|诉讼|仲裁|减持|质押|冻结",
    "capital_action": r"定增|向特定对象发行|可转债|回购|股权激励|员工持股",
}

PDF_KEYWORD_GROUPS = {
    "客户/认证": r"客户|认证|供应商|合格供应商|通过认证|量产认证",
    "订单/合同": r"订单|合同|中标|项目|框架协议|采购协议",
    "产能/扩产": r"产能|扩产|募投|投产|产线|达产|建设项目",
    "盈利质量": r"毛利率|净利率|扣非|经营活动产生的现金流量净额|现金流",
    "资产质量": r"应收账款|存货|商誉|减值|坏账|周转",
    "监管/问询": r"问询函|关注函|监管函|回复|说明|核查",
    "风险事件": r"风险|诉讼|仲裁|处罚|违规|减持|质押|冻结|立案",
    "产业关键词": r"AI|人工智能|算力|CPO|光模块|芯片|半导体|存储|先进封装|服务器|PCB",
}

TITLE_COLUMNS = ["公告标题", "title", "标题", "announcementTitle", "secName"]
DATE_COLUMNS = ["公告时间", "公告日期", "publishDate", "announcementTime", "日期", "time"]
URL_COLUMNS = ["公告链接", "url", "链接", "adjunctUrl", "announcementUrl"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build official-source evidence packs for the A-share learning pool.")
    parser.add_argument("--learning-pool-dir", default="/app/data/learning_pool")
    parser.add_argument("--evidence-dir", default="/app/data/evidence_hub")
    parser.add_argument("--report-dir", default="/app/reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--pool-limit", type=int, default=int(os.environ.get("POOL_LIMIT", "5")))
    parser.add_argument("--lookback-days", type=int, default=int(os.environ.get("LOOKBACK_DAYS", "730")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SOURCE_TIMEOUT", "35")))
    parser.add_argument("--codes", default="", help="Comma separated codes. Overrides the learning-pool selection when set.")
    parser.add_argument("--pdf-limit-per-code", type=int, default=int(os.environ.get("PDF_LIMIT_PER_CODE", "3")))
    parser.add_argument("--pdf-timeout", type=int, default=int(os.environ.get("PDF_TIMEOUT", "25")))
    parser.add_argument("--pdf-max-pages", type=int, default=int(os.environ.get("PDF_MAX_PAGES", "10")))
    parser.add_argument("--pdf-max-bytes", type=int, default=int(os.environ.get("PDF_MAX_BYTES", "22000000")))
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
    text = safe_text(raw)
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def parse_dt(value: Any) -> pd.Timestamp | None:
    text = safe_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{13}", text):
        return pd.to_datetime(int(text), unit="ms", errors="coerce")
    if re.fullmatch(r"\d{10}", text):
        return pd.to_datetime(int(text), unit="s", errors="coerce")
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def pick(row: pd.Series, candidates: list[str]) -> str:
    for column in candidates:
        if column in row.index:
            value = safe_text(row[column])
            if value:
                return value
    return ""


def normalize_url(url: str) -> str:
    url = safe_text(url)
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return "http://static.cninfo.com.cn/" + url.lstrip("/")


def classify_title(title: str) -> str:
    title = safe_text(title)
    matched = []
    for category, pattern in DOC_CATEGORIES.items():
        if re.search(pattern, title):
            matched.append(category)
    return ",".join(matched) if matched else "other_official"


def announcement_id_from_url(url: str) -> str:
    url = safe_text(url)
    for pattern in [r"announcementId=(\d+)", r"/(\d+)\.pdf", r"(\d{8,})"]:
        match = re.search(pattern, url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def org_id_from_url(url: str) -> str:
    match = re.search(r"orgId=([^&]+)", safe_text(url))
    return match.group(1) if match else ""


def pdf_url_from_event(event: dict[str, Any]) -> str:
    announcement_id = announcement_id_from_url(event.get("url", ""))
    event_dt = parse_dt(event.get("event_date", ""))
    if not announcement_id or event_dt is None:
        return ""
    return f"http://static.cninfo.com.cn/finalpage/{event_dt:%Y-%m-%d}/{announcement_id}.pdf"


def lookup_adjunct_pdf_url(event: dict[str, Any], timeout: int) -> tuple[str, str]:
    announcement_id = announcement_id_from_url(event.get("url", ""))
    org_id = org_id_from_url(event.get("url", ""))
    code = normalize_code(event.get("code"))
    event_dt = parse_dt(event.get("event_date", ""))
    if not announcement_id or not org_id or not code or event_dt is None:
        return "", "missing announcementId/orgId/code/date"
    try:
        import requests

        date_text = event_dt.strftime("%Y-%m-%d")
        payload = {
            "pageNum": "1",
            "pageSize": "50",
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{date_text}~{date_text}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        response = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query", data=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        for item in data.get("announcements", []) or []:
            if safe_text(item.get("announcementId")) != announcement_id:
                continue
            adjunct_url = safe_text(item.get("adjunctUrl"))
            if adjunct_url:
                return normalize_url(adjunct_url), "adjunctUrl"
            return "", "announcement matched but adjunctUrl missing"
        return "", "announcementId not found in same-day query"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:240]}"


def pdf_priority(event: dict[str, Any]) -> tuple[int, str]:
    category = safe_text(event.get("category"))
    title = safe_text(event.get("title"))
    score = 10
    if "periodic_report" in category:
        score += 80
    if "regulatory_inquiry" in category:
        score += 75
    if "project_order" in category:
        score += 55
    if "risk_warning" in category:
        score += 55
    if re.search(r"回复|问询函|关注函|监管函", title):
        score += 20
    if re.search(r"年度报告|季度报告|半年度报告", title):
        score += 15
    if re.search(r"摘要|提示性公告|更正公告", title):
        score -= 15
    return score, safe_text(event.get("event_date"))


def candidate_pdf_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates = []
    seen_ids = set()
    for event in events:
        if event.get("source_type") != "official_disclosure":
            continue
        pdf_url = pdf_url_from_event(event)
        announcement_id = announcement_id_from_url(pdf_url)
        if not pdf_url or announcement_id in seen_ids:
            continue
        title = safe_text(event.get("title"))
        category = safe_text(event.get("category"))
        if (
            "other_official" not in category
            or re.search(r"年度报告|季度报告|半年度报告|问询|回复|风险|合同|中标|客户|认证|投资|扩产|产能", title)
        ):
            event = dict(event)
            event["_pdf_url"] = pdf_url
            event["_announcement_id"] = announcement_id
            candidates.append(event)
            seen_ids.add(announcement_id)
    return sorted(candidates, key=pdf_priority, reverse=True)[:limit]


def download_pdf(url: str, path: Path, timeout: int, max_bytes: int) -> tuple[bool, str]:
    if path.exists() and path.stat().st_size > 1000:
        return True, "cached"
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 AStockResearchBot/1.0",
            "Accept": "application/pdf,*/*",
            "Referer": "http://www.cninfo.com.cn/",
        }
        response = requests.get(url, headers=headers, timeout=timeout, stream=True)
        if response.status_code != 200:
            return False, f"http_status={response.status_code}"
        length = int(response.headers.get("Content-Length") or 0)
        if length and length > max_bytes:
            return False, f"pdf_too_large={length}"
        path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        chunks: list[bytes] = []
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return False, f"pdf_too_large_stream={total}"
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data.startswith(b"%PDF"):
            return False, "not_pdf_content"
        path.write_bytes(data)
        return True, f"downloaded={len(data)}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:240]}"


def extract_pdf_text(path: Path, max_pages: int) -> tuple[str, str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        texts = []
        for page in reader.pages[:max_pages]:
            texts.append(page.extract_text() or "")
        text = "\n".join(texts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip(), f"pages={min(len(reader.pages), max_pages)}/{len(reader.pages)}"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:240]}"


def summarize_pdf_text(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", safe_text(text))
    hits: dict[str, int] = {}
    for label, pattern in PDF_KEYWORD_GROUPS.items():
        hits[label] = len(re.findall(pattern, compact, flags=re.IGNORECASE))
    top_hits = [f"{label}{count}" for label, count in sorted(hits.items(), key=lambda item: item[1], reverse=True) if count > 0]
    warnings = []
    for label in ["监管/问询", "风险事件", "资产质量"]:
        if hits.get(label, 0) > 0:
            warnings.append(label)
    summary = "；".join(top_hits[:6]) if top_hits else "未命中核心关键词，需人工打开 PDF 复核"
    if warnings:
        summary += f"；重点复核：{'/'.join(warnings)}"
    return {
        "pdf_text_chars": len(compact),
        "pdf_keyword_hits": json.dumps(hits, ensure_ascii=False),
        "pdf_summary": summary,
        "pdf_excerpt": compact[:800],
    }


def call_source(fn_name: str, kwargs: dict[str, Any], timeout: int) -> tuple[pd.DataFrame, str]:
    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{fn_name} timeout after {timeout}s")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout)
    try:
        data = getattr(ak, fn_name)(**kwargs)
        if isinstance(data, pd.DataFrame):
            return data.head(500), ""
        return pd.DataFrame(), ""
    except TimeoutError:
        return pd.DataFrame(), f"{fn_name} timeout after {timeout}s"
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:500]}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def load_targets(args: argparse.Namespace) -> tuple[pd.DataFrame, list[str]]:
    pool_dir = Path(args.learning_pool_dir)
    pool_path = pool_dir / "current_learning_pool.csv"
    stock_list_path = pool_dir / "current_stock_list.txt"

    if args.codes.strip():
        codes = [normalize_code(code) for code in args.codes.split(",") if normalize_code(code)]
        return pd.DataFrame({"code": codes, "name": [""] * len(codes), "score": [math.nan] * len(codes)}), codes

    if not pool_path.exists():
        raise SystemExit(f"missing learning pool: {pool_path}")

    pool = pd.read_csv(pool_path, dtype={"code": str})
    pool["code"] = pool["code"].map(lambda value: normalize_code(value).zfill(6))
    if "rank" in pool.columns:
        pool = pool.sort_values("rank")
    elif "score" in pool.columns:
        pool = pool.sort_values("score", ascending=False)

    selected: list[str] = []
    if stock_list_path.exists():
        selected = [normalize_code(code).zfill(6) for code in stock_list_path.read_text(encoding="utf-8").split(",") if normalize_code(code)]

    target_codes: list[str] = []
    for code in selected:
        if code and code not in target_codes:
            target_codes.append(code)
    for code in pool["code"].tolist():
        if code and code not in target_codes:
            target_codes.append(code)
        if len(target_codes) >= args.pool_limit:
            break
    target_codes = target_codes[: args.pool_limit]
    return pool, target_codes


def normalize_disclosure_rows(code: str, name: str, raw: pd.DataFrame, source_name: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if raw.empty:
        return events
    for _, row in raw.iterrows():
        row_code = normalize_code(pick(row, ["代码", "股票代码", "证券代码", "stockCode"])) or code
        title = pick(row, TITLE_COLUMNS)
        if not title:
            continue
        event_dt = parse_dt(pick(row, DATE_COLUMNS))
        url = normalize_url(pick(row, URL_COLUMNS))
        row_name = pick(row, ["简称", "公司简称", "证券简称", "name"]) or name
        events.append(
            {
                "code": row_code.zfill(6),
                "name": row_name,
                "event_date": event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt is not None else "",
                "source_type": "official_disclosure",
                "source_name": source_name,
                "title": title,
                "url": url,
                "category": classify_title(title),
                "evidence_level": "high",
            }
        )
    return events


def collect_disclosures(code: str, name: str, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    end_dt = datetime.strptime(args.date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=args.lookback_days)
    start = start_dt.strftime("%Y%m%d")
    end = end_dt.strftime("%Y%m%d")
    calls = [
        ("stock_zh_a_disclosure_report_cninfo", {"symbol": code, "market": "沪深京", "start_date": start, "end_date": end}),
        ("stock_zh_a_disclosure_relation_cninfo", {"symbol": code, "market": "沪深京", "start_date": start, "end_date": end}),
    ]
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for fn_name, kwargs in calls:
        raw, error = call_source(fn_name, kwargs, args.timeout)
        if error:
            errors.append(error)
        events.extend(normalize_disclosure_rows(code, name, raw, fn_name))
        time.sleep(0.4)

    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (event["title"], event["event_date"][:10])
        dedup[key] = event
    ordered = sorted(dedup.values(), key=lambda item: item.get("event_date") or "", reverse=True)
    return ordered[:80], errors


def collect_interactions(code: str, name: str, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    raw, error = call_source("stock_irm_cninfo", {"symbol": code}, args.timeout)
    errors = [error] if error else []
    events: list[dict[str, Any]] = []
    if raw.empty:
        return events, errors

    for _, row in raw.head(60).iterrows():
        question = pick(row, ["问题", "question"])
        answer = pick(row, ["回答内容", "answer"])
        if not question and not answer:
            continue
        update_time = parse_dt(pick(row, ["更新时间", "回答时间", "提问时间"]))
        title = f"问：{question}"
        if answer:
            title += f" / 答：{answer[:180]}"
        events.append(
            {
                "code": code,
                "name": pick(row, ["公司简称", "简称"]) or name,
                "event_date": update_time.strftime("%Y-%m-%d %H:%M:%S") if update_time is not None else "",
                "source_type": "irm_cninfo",
                "source_name": "stock_irm_cninfo",
                "title": title,
                "url": "",
                "category": "interactive_qa_answered" if answer else "interactive_qa_unanswered",
                "evidence_level": "medium" if answer else "low",
            }
        )
    return sorted(events, key=lambda item: item.get("event_date") or "", reverse=True)[:20], errors


def collect_pdf_evidence(code: str, name: str, events: list[dict[str, Any]], args: argparse.Namespace, date_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    pdf_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if args.pdf_limit_per_code <= 0:
        return pdf_rows, errors

    raw_dir = Path(args.evidence_dir) / "pdf_raw" / date_key / code
    text_dir = Path(args.evidence_dir) / "pdf_text" / date_key / code
    for event in candidate_pdf_events(events, args.pdf_limit_per_code):
        resolved_url, resolve_note = lookup_adjunct_pdf_url(event, args.pdf_timeout)
        pdf_url = resolved_url or event["_pdf_url"]
        announcement_id = event["_announcement_id"]
        pdf_path = raw_dir / f"{announcement_id}.pdf"
        text_path = text_dir / f"{announcement_id}.txt"
        ok, download_note = download_pdf(pdf_url, pdf_path, args.pdf_timeout, args.pdf_max_bytes)
        row = {
            "code": code,
            "name": name,
            "event_date": event.get("event_date", ""),
            "source_type": "official_pdf_text",
            "source_name": "cninfo_static_pdf+pypdf",
            "title": event.get("title", ""),
            "url": pdf_url,
            "category": f"{event.get('category', '')},pdf_fulltext",
            "evidence_level": "high" if ok else "low",
            "announcement_id": announcement_id,
            "pdf_status": "downloaded" if ok else "download_failed",
            "pdf_note": f"{resolve_note}; {download_note}",
            "pdf_path": str(pdf_path),
            "pdf_text_path": str(text_path),
            "pdf_text_chars": 0,
            "pdf_keyword_hits": "{}",
            "pdf_summary": "",
            "pdf_excerpt": "",
        }
        if not ok:
            errors.append(f"{announcement_id}: {resolve_note}; {download_note}")
            pdf_rows.append(row)
            continue

        text, extract_note = extract_pdf_text(pdf_path, args.pdf_max_pages)
        row["pdf_note"] = f"{resolve_note}; {download_note}; {extract_note}"
        if not text:
            row["pdf_status"] = "extract_failed"
            row["evidence_level"] = "medium"
            errors.append(f"{announcement_id}: {extract_note}")
        else:
            text_dir.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text, encoding="utf-8")
            row["pdf_status"] = "parsed"
            row.update(summarize_pdf_text(text))
        pdf_rows.append(row)
        time.sleep(0.3)
    return pdf_rows, errors


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
                number = safe_float(value)
                if not math.isnan(number):
                    return number
    return math.nan


def describe_number(value: float, suffix: str = "") -> str:
    if math.isnan(value):
        return "缺失"
    return f"{value:.2f}{suffix}"


def financial_score(metrics: dict[str, float]) -> tuple[float, list[str], list[str]]:
    score = 50.0
    positives: list[str] = []
    warnings: list[str] = []

    revenue_yoy = metrics["revenue_yoy"]
    if not math.isnan(revenue_yoy):
        if revenue_yoy >= 20:
            score += 12
            positives.append("营收同比增长较强")
        elif revenue_yoy > 0:
            score += 5
            positives.append("营收同比为正")
        elif revenue_yoy <= -20:
            score -= 18
            warnings.append("营收同比明显下滑")
        else:
            score -= 8
            warnings.append("营收同比下滑")

    profit_yoy = metrics["profit_yoy"]
    if not math.isnan(profit_yoy):
        if profit_yoy >= 30:
            score += 16
            positives.append("扣非/归母利润同比弹性较强")
        elif profit_yoy > 0:
            score += 7
            positives.append("利润同比为正")
        elif profit_yoy <= -30:
            score -= 22
            warnings.append("利润同比大幅下滑")
        else:
            score -= 12
            warnings.append("利润同比下滑")

    ocf = metrics["operating_cash_flow"]
    if not math.isnan(ocf):
        if ocf > 0:
            score += 10
            positives.append("经营现金流为正")
        else:
            score -= 15
            warnings.append("经营现金流为负")

    roe = metrics["roe"]
    if not math.isnan(roe):
        if roe >= 8:
            score += 8
            positives.append("ROE 达到较好水平")
        elif roe < 3:
            score -= 8
            warnings.append("ROE 偏低")

    gross_margin = metrics["gross_margin"]
    if not math.isnan(gross_margin):
        if gross_margin >= 25:
            score += 5
            positives.append("毛利率较高")
        elif gross_margin < 10:
            score -= 6
            warnings.append("毛利率偏低")

    debt_ratio = metrics["debt_ratio"]
    if not math.isnan(debt_ratio):
        if debt_ratio <= 55:
            score += 4
            positives.append("资产负债率相对可控")
        elif debt_ratio >= 75:
            score -= 10
            warnings.append("资产负债率偏高")

    missing = [key for key, value in metrics.items() if key not in {"raw_rows"} and math.isnan(value)]
    if len(missing) >= 4:
        score -= 8
        warnings.append("关键财务指标缺失较多")

    return clamp(score), positives, warnings


def collect_financial_quality(code: str, name: str, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    raw, error = call_source("stock_financial_abstract_new_ths", {"symbol": code}, args.timeout)
    errors = [error] if error else []
    if raw.empty:
        return {
            "code": code,
            "name": name,
            "report_date": "",
            "financial_quality_score": 35.0,
            "financial_notes": "财务摘要接口未返回数据",
            "financial_warnings": "财务数据缺失，不能做强结论",
            "available_metric_count": 0,
        }, errors

    raw = raw.copy()
    if "report_date" in raw.columns:
        raw["_report_date"] = pd.to_datetime(raw["report_date"], errors="coerce")
    else:
        raw["_report_date"] = pd.NaT
    latest_date = raw["_report_date"].dropna().max()
    if pd.isna(latest_date):
        latest = raw.head(120)
        report_date = ""
    else:
        latest = raw[raw["_report_date"] == latest_date]
        report_date = latest_date.strftime("%Y-%m-%d")

    metrics = {
        "revenue_yoy": metric_value(latest, [r"operat.*income.*yoy", r"revenue.*yoy", r"营业.*收入.*同比", r"income_yoy"], ["value", "yoy", "single_yoy"]),
        "profit_yoy": metric_value(latest, [r"deduct_net_profit_yoy", r"parent.*net.*profit.*yoy", r"net_profit.*yoy", r"归母.*同比", r"扣非.*同比"], ["value", "yoy", "single_yoy"]),
        "operating_cash_flow": metric_value(latest, [r"operating_cash_flow", r"per_operating_cash_flow_net", r"经营.*现金"], ["value", "single"]),
        "roe": metric_value(latest, [r"\broe\b", r"net_asset_yield", r"净资产收益率"], ["value", "single"]),
        "gross_margin": metric_value(latest, [r"gross_profit", r"毛利率"], ["value", "single"]),
        "debt_ratio": metric_value(latest, [r"asset_liability", r"资产负债率"], ["value", "single"]),
    }
    score, positives, warnings = financial_score(metrics)
    metric_summary = [
        f"营收同比={describe_number(metrics['revenue_yoy'], '%')}",
        f"利润同比={describe_number(metrics['profit_yoy'], '%')}",
        f"经营现金流={describe_number(metrics['operating_cash_flow'])}",
        f"ROE={describe_number(metrics['roe'], '%')}",
        f"毛利率={describe_number(metrics['gross_margin'], '%')}",
        f"资产负债率={describe_number(metrics['debt_ratio'], '%')}",
    ]
    return {
        "code": code,
        "name": name,
        "report_date": report_date,
        "financial_quality_score": round(score, 2),
        "financial_notes": "；".join(positives + metric_summary),
        "financial_warnings": "；".join(warnings) if warnings else "暂无硬性财务红旗",
        "available_metric_count": sum(0 if math.isnan(value) else 1 for value in metrics.values()),
        **{f"metric_{key}": value for key, value in metrics.items()},
    }, errors


def evidence_quality(events: list[dict[str, Any]], financial_row: dict[str, Any], as_of: datetime) -> tuple[float, str, str]:
    official = [event for event in events if event["source_type"] == "official_disclosure"]
    parsed_pdfs = [event for event in events if event["source_type"] == "official_pdf_text" and event.get("pdf_status") == "parsed"]
    interactions = [event for event in events if event["source_type"] == "irm_cninfo"]
    periodic = [event for event in official if "periodic_report" in event["category"]]
    regulatory = [event for event in official if "regulatory_inquiry" in event["category"]]
    projects = [event for event in official if "project_order" in event["category"]]
    risks = [event for event in official if "risk_warning" in event["category"]]

    latest_doc_age_score = 0.0
    dates = [parse_dt(event.get("event_date")) for event in official]
    dates = [date for date in dates if date is not None]
    if dates:
        age_days = (as_of - max(dates).to_pydatetime()).days
        if age_days <= 180:
            latest_doc_age_score = 10
        elif age_days <= 365:
            latest_doc_age_score = 6
        elif age_days <= 730:
            latest_doc_age_score = 3

    score = 0.0
    score += min(len(official) * 3, 25)
    score += min(len(periodic) * 7, 24)
    score += min(len(regulatory) * 4, 12)
    score += min(len(projects) * 4, 12)
    score += min(len(interactions) * 2, 10)
    score += min(len(parsed_pdfs) * 5, 10)
    score += 17 if financial_row.get("available_metric_count", 0) >= 3 else 8
    score += latest_doc_age_score

    notes = [
        f"官方公告{len(official)}条",
        f"定期报告{len(periodic)}条",
        f"问询/监管{len(regulatory)}条",
        f"项目/订单/客户线索{len(projects)}条",
        f"互动易/投资者问答{len(interactions)}条",
        f"PDF全文解析{len(parsed_pdfs)}份",
    ]
    warnings = []
    if not official:
        warnings.append("官方公告接口未返回有效数据")
    if not periodic:
        warnings.append("近两年定期报告元数据缺失")
    if not parsed_pdfs:
        warnings.append("公告 PDF 正文未完成解析，证据仍停留在标题/元数据层")
    if risks:
        warnings.append(f"发现{len(risks)}条风险类标题，需要人工复核")
    if financial_row.get("available_metric_count", 0) < 3:
        warnings.append("财务可解析指标不足")
    return clamp(score), "；".join(notes), "；".join(warnings) if warnings else "证据覆盖尚可"


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


def final_action(final_score: float, ai_score: float, advice: str, trend: str, evidence_score: float, missing_ai: bool = False) -> tuple[str, str]:
    if missing_ai:
        return "等待AI分析", "基本面池和证据层已生成，但 daily_stock_analysis 尚未对该股生成当天 AI 报告。"
    negative_ai = ai_score < 45 and (any(word in advice for word in ["卖出", "减仓"]) or any(word in trend for word in ["看空", "下跌"]))
    if negative_ai:
        return "降级", "AI 技术/趋势层明显不支持，先不进入买入计划。"
    if evidence_score < 45:
        return "证据不足", "官方证据覆盖不够，不能把高分当成强结论。"
    if final_score >= 72 and ai_score >= 55:
        return "优先深挖", "规则层、AI 层、财务和证据层没有明显冲突。"
    if final_score >= 58:
        return "观察", "保留观察，等待价格结构或新证据确认。"
    return "降级", "综合分不足，暂不作为优先研究对象。"


def build_final_scores(pool: pd.DataFrame, target_codes: list[str], evidence_rows: list[dict[str, Any]], financial_rows: list[dict[str, Any]], report_dir: Path, date_key: str) -> pd.DataFrame:
    ai = parse_ai_summary(report_dir / f"report_{date_key}.md")
    evidence_by_code = {row["code"]: row for row in evidence_rows}
    financial_by_code = {row["code"]: row for row in financial_rows}
    pool_by_code = {str(row["code"]).zfill(6): row for _, row in pool.iterrows()}

    output: list[dict[str, Any]] = []
    for code in target_codes:
        pool_row = pool_by_code.get(code)
        learning_score = safe_float(pool_row.get("score") if pool_row is not None else math.nan, 50.0)
        name = safe_text(pool_row.get("name") if pool_row is not None else "") or code
        ai_row = ai.get(code, {})
        missing_ai = not bool(ai_row)
        ai_score = safe_float(ai_row.get("ai_score"), 0.0 if missing_ai else 50.0)
        advice = ai_row.get("advice", "未生成")
        trend = ai_row.get("trend", "待分析" if missing_ai else "未知")
        evidence_score = safe_float(evidence_by_code.get(code, {}).get("evidence_quality_score"), 35.0)
        finance_score = safe_float(financial_by_code.get(code, {}).get("financial_quality_score"), 35.0)
        raw_final = 0.25 * learning_score + 0.25 * ai_score + 0.30 * finance_score + 0.20 * evidence_score
        negative_ai = ai_score < 45 and (any(word in advice for word in ["卖出", "减仓"]) or any(word in trend for word in ["看空", "下跌"]))
        capped_final = min(raw_final, 55.0) if negative_ai else raw_final
        if missing_ai:
            capped_final = min(capped_final, 50.0)
        action, reason = final_action(capped_final, ai_score, advice, trend, evidence_score, missing_ai=missing_ai)
        output.append(
            {
                "code": code,
                "name": name,
                "learning_score": round(learning_score, 2),
                "ai_score": round(ai_score, 2),
                "financial_quality_score": round(finance_score, 2),
                "evidence_quality_score": round(evidence_score, 2),
                "final_research_score": round(clamp(capped_final), 2),
                "action": action,
                "reason": reason,
                "ai_advice": advice,
                "ai_trend": trend,
            }
        )
    return pd.DataFrame(output).sort_values("final_research_score", ascending=False)


def write_pack(code: str, name: str, events: list[dict[str, Any]], financial_row: dict[str, Any], evidence_row: dict[str, Any], final_row: dict[str, Any], pack_dir: Path) -> None:
    official = [event for event in events if event["source_type"] == "official_disclosure"]
    pdf_docs = [event for event in events if event["source_type"] == "official_pdf_text"]
    interactions = [event for event in events if event["source_type"] == "irm_cninfo"]
    lines = [
        f"# {name}({code}) 证据包 V2",
        "",
        f"- 最终研究分：{final_row.get('final_research_score', '')}",
        f"- 动作：{final_row.get('action', '')}",
        f"- 降级/排序理由：{final_row.get('reason', '')}",
        f"- 证据质量分：{evidence_row.get('evidence_quality_score', '')}",
        f"- 财务质量分：{financial_row.get('financial_quality_score', '')}",
        "",
        "## 官方公告线索",
    ]
    if official:
        for event in official[:12]:
            link = f"[链接]({event['url']})" if event.get("url") else "无链接"
            lines.append(f"- {event.get('event_date', '')[:10]} | {event.get('category', '')} | {event.get('title', '')} | {link}")
    else:
        lines.append("- 未抓到官方公告元数据，需要人工补查巨潮/交易所公告。")

    lines += ["", "## 公告 PDF 全文解析"]
    if pdf_docs:
        for event in pdf_docs[:8]:
            link = f"[PDF]({event['url']})" if event.get("url") else "无链接"
            summary = safe_text(event.get("pdf_summary")) or safe_text(event.get("pdf_note"))
            chars = safe_float(event.get("pdf_text_chars"), 0.0)
            lines.append(
                f"- {event.get('event_date', '')[:10]} | {event.get('pdf_status', '')} | {chars:.0f}字 | {event.get('title', '')} | {summary} | {link}"
            )
    else:
        lines.append("- 未完成公告 PDF 正文解析。若是接口、PDF 过大或无文字层，会降级为标题证据。")

    lines += ["", "## 互动易 / 投资者关系"]
    if interactions:
        for event in interactions[:8]:
            lines.append(f"- {event.get('event_date', '')[:10]} | {event.get('category', '')} | {event.get('title', '')[:240]}")
    else:
        lines.append("- 未抓到互动易数据。")

    lines += [
        "",
        "## 财务质量",
        f"- 最新报告期：{financial_row.get('report_date', '') or '缺失'}",
        f"- 可解析指标数：{financial_row.get('available_metric_count', 0)}",
        f"- 正向线索：{financial_row.get('financial_notes', '')}",
        f"- 风险/缺口：{financial_row.get('financial_warnings', '')}",
        "",
        "## 下一步人工复核",
        "- 打开最新年报/季报 PDF，核对收入构成、毛利率、存货、应收账款和经营现金流；若本证据包已有 PDF 摘要，先复核命中关键词所在章节。",
        "- 对项目/订单/客户认证类公告追问：金额、交付周期、毛利率、是否已经进入收入。",
        "- 对问询函/监管函追问：问题是否已经闭环，是否涉及收入确认、关联交易、资金占用或商誉。",
    ]
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / f"{code}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_reports(date_key: str, report_dir: Path, evidence_df: pd.DataFrame, final_df: pd.DataFrame, source_errors: list[dict[str, str]]) -> None:
    evidence_path = report_dir / f"evidence_quality_{date_key}.md"
    final_path = report_dir / f"final_score_{date_key}.md"
    report_dir.mkdir(parents=True, exist_ok=True)

    evidence_lines = [
        f"# 数据源质量层 V2 - {datetime.now():%Y-%m-%d}",
        "",
        "定位：用官方公告、公告 PDF 全文解析、互动易和财务摘要给基本面池股票建立可审计证据包。V2 仍先做小批量稳定跑通，不追求全市场覆盖。",
        "",
        "| 代码 | 名称 | 证据质量分 | 证据覆盖 | 质量警告 |",
        "|---|---|---:|---|---|",
    ]
    for _, row in evidence_df.sort_values("evidence_quality_score", ascending=False).iterrows():
        evidence_lines.append(
            f"| {row['code']} | {row['name']} | {row['evidence_quality_score']:.2f} | {row['evidence_notes']} | {row['evidence_warnings']} |"
        )
    if source_errors:
        evidence_lines += ["", "## 数据源错误"]
        for item in source_errors[:30]:
            evidence_lines.append(f"- {item['code']} {item['source']}: {item['error']}")
    evidence_path.write_text("\n".join(evidence_lines).rstrip() + "\n", encoding="utf-8")

    final_lines = [
        f"# 最终研究排序 V1 - {datetime.now():%Y-%m-%d}",
        "",
        "公式：25% 基础池规则分 + 25% daily_stock_analysis AI 分 + 30% 财务质量分 + 20% 证据质量分。若 AI 技术/趋势明显看空，最终分上限为 55。",
        "",
        "| 排名 | 代码 | 名称 | 最终分 | 动作 | 基础池 | AI | 财务 | 证据 | 理由 |",
        "|---:|---|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for idx, row in enumerate(final_df.itertuples(index=False), start=1):
        final_lines.append(
            f"| {idx} | {row.code} | {row.name} | {row.final_research_score:.2f} | {row.action} | {row.learning_score:.2f} | {row.ai_score:.2f} | {row.financial_quality_score:.2f} | {row.evidence_quality_score:.2f} | {row.reason} |"
        )
    final_lines += [
        "",
        "## 使用规则",
        "- `优先深挖` 只是研究优先级，不等于买入信号。",
        "- `证据不足` 表示数据源覆盖不够，不能把模型结论当成硬判断。",
        "- `降级` 表示至少有一层与买入逻辑冲突，先进入复盘/观察。",
    ]
    final_path.write_text("\n".join(final_lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    evidence_dir = Path(args.evidence_dir)
    report_dir = Path(args.report_dir)
    date_key = args.date
    as_of = datetime.strptime(date_key, "%Y%m%d")
    raw_dir = evidence_dir / "raw_events"
    pack_dir = evidence_dir / "evidence_pack" / date_key
    raw_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    pool, target_codes = load_targets(args)
    pool_by_code = {str(row["code"]).zfill(6): row for _, row in pool.iterrows()}
    all_events: list[dict[str, Any]] = []
    all_pdf_rows: list[dict[str, Any]] = []
    financial_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    source_errors: list[dict[str, str]] = []

    for code in target_codes:
        pool_row = pool_by_code.get(code)
        name = safe_text(pool_row.get("name") if pool_row is not None else "") or code
        print(f"[evidence] {code} {name}", flush=True)
        disclosures, disclosure_errors = collect_disclosures(code, name, args)
        interactions, interaction_errors = collect_interactions(code, name, args)
        pdf_rows, pdf_errors = collect_pdf_evidence(code, name, disclosures, args, date_key)
        financial_row, financial_errors = collect_financial_quality(code, name, args)
        events = disclosures + interactions + pdf_rows
        for error in disclosure_errors:
            source_errors.append({"code": code, "source": "cninfo_disclosure", "error": error})
        for error in interaction_errors:
            source_errors.append({"code": code, "source": "irm_cninfo", "error": error})
        for error in pdf_errors:
            source_errors.append({"code": code, "source": "cninfo_pdf_text", "error": error})
        for error in financial_errors:
            source_errors.append({"code": code, "source": "financial_abstract", "error": error})

        score, notes, warnings = evidence_quality(events, financial_row, as_of)
        evidence_row = {
            "code": code,
            "name": name,
            "evidence_quality_score": round(score, 2),
            "evidence_notes": notes,
            "evidence_warnings": warnings,
        }
        all_events.extend(events)
        all_pdf_rows.extend(pdf_rows)
        financial_rows.append(financial_row)
        evidence_rows.append(evidence_row)
        time.sleep(0.5)

    events_df = pd.DataFrame(all_events)
    pdf_df = pd.DataFrame(all_pdf_rows)
    financial_df = pd.DataFrame(financial_rows)
    evidence_df = pd.DataFrame(evidence_rows)
    final_df = build_final_scores(pool, target_codes, evidence_rows, financial_rows, report_dir, date_key)

    events_df.to_csv(evidence_dir / f"evidence_events_{date_key}.csv", index=False, encoding="utf-8-sig")
    events_df.to_json(evidence_dir / f"evidence_events_{date_key}.json", orient="records", force_ascii=False, indent=2)
    pdf_df.to_csv(evidence_dir / f"pdf_evidence_{date_key}.csv", index=False, encoding="utf-8-sig")
    pdf_df.to_json(evidence_dir / f"pdf_evidence_{date_key}.json", orient="records", force_ascii=False, indent=2)
    financial_df.to_csv(evidence_dir / f"financial_quality_{date_key}.csv", index=False, encoding="utf-8-sig")
    financial_df.to_json(evidence_dir / f"financial_quality_{date_key}.json", orient="records", force_ascii=False, indent=2)
    evidence_df.to_csv(evidence_dir / f"evidence_quality_{date_key}.csv", index=False, encoding="utf-8-sig")
    final_df.to_csv(evidence_dir / f"final_score_{date_key}.csv", index=False, encoding="utf-8-sig")
    final_df.to_json(evidence_dir / f"final_score_{date_key}.json", orient="records", force_ascii=False, indent=2)
    (raw_dir / f"source_errors_{date_key}.json").write_text(json.dumps(source_errors, ensure_ascii=False, indent=2), encoding="utf-8")

    final_by_code = {row["code"]: row for _, row in final_df.iterrows()}
    event_by_code: dict[str, list[dict[str, Any]]] = {}
    for event in all_events:
        event_by_code.setdefault(event["code"], []).append(event)
    evidence_by_code = {row["code"]: row for row in evidence_rows}
    financial_by_code = {row["code"]: row for row in financial_rows}
    for code in target_codes:
        pool_row = pool_by_code.get(code)
        name = safe_text(pool_row.get("name") if pool_row is not None else "") or code
        write_pack(
            code,
            name,
            event_by_code.get(code, []),
            financial_by_code.get(code, {}),
            evidence_by_code.get(code, {}),
            final_by_code.get(code, {}),
            pack_dir,
        )

    write_reports(date_key, report_dir, evidence_df, final_df, source_errors)
    print(report_dir / f"final_score_{date_key}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
