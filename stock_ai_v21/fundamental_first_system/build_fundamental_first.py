from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='build fundamental first gate')
    parser.add_argument('--fundamental-pool-dir', default='/app/data/fundamental_pool')
    parser.add_argument('--evidence-dir', default='/app/data/evidence_hub')
    parser.add_argument('--valuation-dir', default='/app/data/valuation_layer')
    parser.add_argument('--final-layer-dir', default='/app/data/final_layers')
    parser.add_argument('--historical-backtest-dir', default='/app/data/historical_backtest')
    parser.add_argument('--financial-statements-dir', default='/app/data/financial_statements')
    parser.add_argument('--data-quality-dir', default='/app/data/data_quality')
    parser.add_argument('--output-dir', default='/app/data/fundamental_first')
    parser.add_argument('--report-dir', default='/app/reports')
    parser.add_argument('--date', default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument('--max-candidates', type=int, default=50)
    parser.add_argument('--entry-threshold', type=float, default=76.0)
    parser.add_argument('--min-financial-score', type=float, default=65.0)
    parser.add_argument('--min-evidence-score', type=float, default=55.0)
    parser.add_argument('--min-expectation-gap-score', type=float, default=55.0)
    parser.add_argument('--min-financial-statement-score', type=float, default=55.0)
    parser.add_argument('--min-data-quality-score', type=float, default=65.0)
    return parser.parse_args()


def safe_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        text = safe_text(value).replace(',', '').replace('%', '')
        if text in {'', '-', '--', 'nan', 'None'}:
            return default
        number = float(text)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def coalesce_float(*values: Any, default: float = math.nan) -> float:
    for value in values:
        number = safe_float(value, math.nan)
        if not math.isnan(number):
            return number
    return default


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def normalize_code(value: Any) -> str:
    match = re.search(r'(\d{6})', safe_text(value))
    return match.group(1) if match else safe_text(value).zfill(6)[-6:]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={'code': str}, encoding='utf-8-sig')


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def latest_file(directory: Path, pattern: str, date_key: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    eligible = []
    for path in files:
        match = re.search(r'(\d{8})', path.name)
        if match and match.group(1) <= date_key:
            eligible.append(path)
    return eligible[-1] if eligible else (files[-1] if files else None)


def adjusted_score_v21(row: pd.Series) -> float:
    name = safe_text(row.get('name'))
    if 'ST' in name.upper() or '退' in name:
        return 0.0
    ret20 = safe_float(row.get('ret20'), 0.0)
    ret60 = safe_float(row.get('ret60'), 0.0)
    ret120 = safe_float(row.get('ret120'), 0.0)
    vol60 = safe_float(row.get('vol60'), 0.0)
    drawdown120 = safe_float(row.get('drawdown120'), 0.0)
    liquidity_score = safe_float(row.get('liquidity_score'), 50.0)
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
    return round(clamp(score), 4)


def load_trade_scores(backtest_dir: Path, date_key: str) -> tuple[dict[str, dict[str, Any]], str]:
    path = latest_file(backtest_dir, 'market_v2_score_table_*.csv', date_key)
    if path is None:
        return {}, 'missing_market_v2_score_table'
    df = read_csv(path)
    if df.empty or 'code' not in df.columns:
        return {}, f'invalid_{path.name}'
    df['code'] = df['code'].map(normalize_code)
    df['date_dt'] = pd.to_datetime(df.get('date'), errors='coerce')
    target_dt = pd.to_datetime(date_key, format='%Y%m%d', errors='coerce')
    if not pd.isna(target_dt):
        df = df[df['date_dt'] <= target_dt]
    df = df.sort_values(['code', 'date_dt']).groupby('code', as_index=False).tail(1)
    result: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = normalize_code(row.get('code'))
        result[code] = {
            'trade_score': adjusted_score_v21(row),
            'trade_score_date': row['date_dt'].strftime('%Y-%m-%d') if not pd.isna(row['date_dt']) else '',
            'ret20': safe_float(row.get('ret20')),
            'ret60': safe_float(row.get('ret60')),
            'ret120': safe_float(row.get('ret120')),
            'vol60': safe_float(row.get('vol60')),
            'drawdown120': safe_float(row.get('drawdown120')),
            'close': safe_float(row.get('close')),
        }
    return result, path.name


def load_market_state(backtest_dir: Path, date_key: str) -> dict[str, Any]:
    files = sorted((backtest_dir / 'cache' / 'indexes').glob('sh000300_*.csv'))
    if not files:
        return {'market_ok': True, 'market_reason': '沪深300缓存缺失，暂不启用大盘闸门'}
    hist = read_csv(files[-1])
    if hist.empty:
        return {'market_ok': True, 'market_reason': '沪深300缓存为空，暂不启用大盘闸门'}
    date_col = 'date' if 'date' in hist.columns else ('日期' if '日期' in hist.columns else None)
    close_col = 'close' if 'close' in hist.columns else ('收盘' if '收盘' in hist.columns else None)
    if not date_col or not close_col:
        return {'market_ok': True, 'market_reason': '沪深300字段不完整，暂不启用大盘闸门'}
    hist = pd.DataFrame({'date': pd.to_datetime(hist[date_col], errors='coerce'), 'close': pd.to_numeric(hist[close_col], errors='coerce')}).dropna().sort_values('date')
    target_dt = pd.to_datetime(date_key, format='%Y%m%d', errors='coerce')
    if not pd.isna(target_dt):
        hist = hist[hist['date'] <= target_dt]
    if len(hist) < 200:
        return {'market_ok': True, 'market_reason': '沪深300历史不足200日，暂不启用大盘闸门'}
    close = float(hist.iloc[-1]['close'])
    ma200 = float(hist.tail(200)['close'].mean())
    close120 = float(hist.iloc[-121]['close']) if len(hist) > 120 else math.nan
    ret120 = close / close120 - 1 if close120 and close120 > 0 else math.nan
    ok = bool(close >= ma200 and (math.isnan(ret120) or ret120 > -0.10))
    return {
        'market_ok': ok,
        'market_reason': '沪深300站上200日线且120日跌幅约束通过' if ok else '沪深300未满足趋势风控',
        'index_close': round(close, 4),
        'index_ma200': round(ma200, 4),
        'index_ret120_pct': round(ret120 * 100, 4) if not math.isnan(ret120) else '',
    }


def add_prefix(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=['code'])
    keep = frame.copy()
    keep['code'] = keep['code'].map(normalize_code)
    return keep.rename(columns={column: f'{prefix}{column}' for column in keep.columns if column != 'code'})


def has_red_flag(text: str) -> bool:
    keys = ['经营现金流为负', '退市', '处罚', '立案', '降级', '无法表示', '保留意见', '三表增强层质量偏弱', '经营现金流/利润偏低']
    return any(key in text for key in keys)


def load_data_quality_state(data_quality_dir: Path) -> dict[str, Any]:
    state = read_json(data_quality_dir / 'current_data_quality.json')
    if not isinstance(state, dict):
        return {'status': 'MISSING', 'overall_score': 75.0, 'critical_block': False, 'notes': '数据质量层缺失，暂按中性处理'}
    return state


def enhanced_financial_score(base_score: float, statement_score: float) -> float:
    if math.isnan(statement_score) or statement_score <= 0:
        return base_score
    return clamp(base_score * 0.62 + statement_score * 0.38)


def classify(row: pd.Series, market_state: dict[str, Any], data_quality_state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    code = normalize_code(row.get('code'))
    name = safe_text(row.get('name')) or code
    trade = row.get('trade') if isinstance(row.get('trade'), dict) else {}
    plan = row.get('plan') if isinstance(row.get('plan'), dict) else {}
    learning_score = coalesce_float(row.get('score'), row.get('preselect_score'), default=0.0)
    pool_score = coalesce_float(row.get('fundamental_score'), default=0.0)
    base_financial_score = coalesce_float(row.get('final_financial_quality_score'), row.get('fin_financial_quality_score'), row.get('fundamental_score'), default=0.0)
    statement_score = coalesce_float(row.get('fs_financial_statement_score'), default=math.nan)
    statement_coverage = coalesce_float(row.get('fs_statement_coverage_score'), default=math.nan)
    financial_score = enhanced_financial_score(base_financial_score, statement_score)
    evidence_raw = coalesce_float(row.get('final_evidence_quality_score'), row.get('evidence_evidence_quality_score'), default=math.nan)
    research_raw = coalesce_float(row.get('final_final_research_score'), default=math.nan)
    valuation_raw = coalesce_float(row.get('val_valuation_score'), default=math.nan)
    expectation_raw = coalesce_float(row.get('val_expectation_gap_score'), default=math.nan)
    has_deep_research = (not math.isnan(evidence_raw) and evidence_raw > 0) or (not math.isnan(research_raw) and research_raw > 0)
    evidence_score = 0.0 if math.isnan(evidence_raw) else evidence_raw
    research_score = 0.0 if math.isnan(research_raw) else research_raw
    valuation_score = 45.0 if math.isnan(valuation_raw) else valuation_raw
    expectation_score = 45.0 if math.isnan(expectation_raw) else expectation_raw
    trade_score = coalesce_float(trade.get('trade_score'), row.get('preselect_score'), default=0.0)
    metric_count = int(coalesce_float(row.get('available_metric_count'), row.get('fin_available_metric_count'), row.get('fs_available_statement_metric_count'), default=0))
    valuation_level = safe_text(row.get('val_valuation_level')) or '估值未确认'
    expectation_gap = safe_text(row.get('val_expectation_gap')) or '预期差未确认'
    data_quality_score = coalesce_float(row.get('dq_data_quality_score'), data_quality_state.get('overall_score'), default=75.0)
    data_quality_status = safe_text(row.get('dq_data_quality_status')) or safe_text(data_quality_state.get('status')) or 'MISSING'
    data_quality_warnings = safe_text(row.get('dq_data_quality_warnings')) or safe_text(data_quality_state.get('notes'))
    global_data_quality_score = safe_float(data_quality_state.get('overall_score'), 75.0)
    global_critical_block = bool(data_quality_state.get('critical_block'))
    warnings = '；'.join([
        safe_text(row.get('fundamental_warnings')),
        safe_text(row.get('fin_financial_warnings')),
        safe_text(row.get('fs_financial_statement_warnings')),
        safe_text(row.get('evidence_evidence_warnings')),
        safe_text(row.get('val_valuation_warning')),
        data_quality_warnings,
        safe_text(plan.get('downgrade_rule')),
    ])
    company_score = clamp(pool_score * 0.38 + financial_score * 0.42 + learning_score * 0.12 + data_quality_score * 0.08)
    industry_score = clamp(evidence_score * 0.60 + research_score * 0.40)
    value_score = clamp(expectation_score * 0.60 + valuation_score * 0.40)
    opportunity_score = clamp(trade_score)
    total_score = clamp(company_score * 0.30 + industry_score * 0.25 + value_score * 0.20 + opportunity_score * 0.25)
    fundamental_gate = financial_score >= args.min_financial_score and pool_score >= 60 and metric_count >= 3
    statement_gate = (not math.isnan(statement_score)) and statement_score >= args.min_financial_statement_score and (math.isnan(statement_coverage) or statement_coverage >= 40)
    data_quality_gate = (not global_critical_block) and global_data_quality_score >= args.min_data_quality_score and data_quality_score >= args.min_data_quality_score and data_quality_status != 'BAD'
    evidence_gate = evidence_score >= args.min_evidence_score and research_score >= 58
    valuation_gate = expectation_score >= args.min_expectation_gap_score and valuation_level not in {'估值极高'} and expectation_gap not in {'成长强但估值透支', '低质量+高估值风险'}
    trade_gate = trade_score >= args.entry_threshold
    market_ok = bool(market_state.get('market_ok'))
    red_flag = has_red_flag(warnings)
    failed = []
    if not data_quality_gate:
        failed.append('数据质量闸门未过')
    if not statement_gate:
        failed.append('财务三表增强闸门未过')
    if not fundamental_gate:
        failed.append('财务/基本面闸门未过')
    if not evidence_gate:
        failed.append('产业证据/最终研究闸门未过')
    if not valuation_gate:
        failed.append('估值/预期差闸门未过')
    if not trade_gate:
        failed.append('交易机会闸门未过')
    if not market_ok:
        failed.append('市场风控未过')
    if red_flag:
        failed.append('存在硬红旗或最终层降级')
    if global_critical_block:
        decision = 'REJECT'
        action = '关键数据源质量不足，暂停升级交易候选'
    elif not has_deep_research and fundamental_gate and statement_gate and data_quality_gate:
        decision = 'PENDING_RESEARCH'
        action = '待深度研究，不做否定判断'
        failed = ['尚未进入本轮公告/证据/估值深度研究']
    elif not red_flag and data_quality_gate and statement_gate and fundamental_gate and evidence_gate and valuation_gate and trade_gate and market_ok:
        decision = 'BUY_READY'
        action = '进入现实模拟盘候选'
    elif not red_flag and data_quality_gate and statement_gate and fundamental_gate and evidence_gate and total_score >= 62:
        decision = 'WATCH'
        action = '保留观察，等待估值或交易机会'
    elif not red_flag and fundamental_gate and (not statement_gate or not data_quality_gate):
        decision = 'PENDING_RESEARCH'
        action = '基本面有线索，但需补齐数据质量或三表验证'
    else:
        decision = 'REJECT'
        action = '不进入交易机会层'
    price = coalesce_float(row.get('price'), plan.get('snapshot_price'), trade.get('close'), default=math.nan)
    stop = coalesce_float(plan.get('risk_stop'), price * 0.88 if not math.isnan(price) else math.nan, default=math.nan)
    return {
        'date': args.date,
        'code': code,
        'name': name,
        'decision': decision,
        'action': action,
        'research_status': 'RESEARCHED' if has_deep_research else 'PENDING_RESEARCH',
        'failed_gates': '；'.join(failed) if failed else '全部通过',
        'fundamental_first_score': round(total_score, 2),
        'company_quality_score': round(company_score, 2),
        'industry_logic_score': round(industry_score, 2),
        'value_gap_score': round(value_score, 2),
        'opportunity_score': round(opportunity_score, 2),
        'learning_score': round(learning_score, 2),
        'pool_fundamental_score': round(pool_score, 2),
        'financial_quality_score': round(financial_score, 2),
        'base_financial_quality_score': round(base_financial_score, 2),
        'financial_statement_score': round(statement_score, 2) if not math.isnan(statement_score) else '',
        'statement_coverage_score': round(statement_coverage, 2) if not math.isnan(statement_coverage) else '',
        'financial_statement_warnings': safe_text(row.get('fs_financial_statement_warnings')),
        'data_quality_score': round(data_quality_score, 2),
        'data_quality_status': data_quality_status,
        'data_quality_warnings': data_quality_warnings,
        'available_metric_count': metric_count,
        'evidence_quality_score': round(evidence_score, 2),
        'final_research_score': round(research_score, 2),
        'valuation_score': round(valuation_score, 2),
        'valuation_level': valuation_level,
        'expectation_gap_score': round(expectation_score, 2),
        'expectation_gap': expectation_gap,
        'trade_score': round(trade_score, 2),
        'trade_score_date': safe_text(trade.get('trade_score_date')),
        'ret20': trade.get('ret20', ''),
        'ret60': trade.get('ret60', ''),
        'ret120': trade.get('ret120', ''),
        'vol60': trade.get('vol60', ''),
        'drawdown120': trade.get('drawdown120', ''),
        'market_ok': market_ok,
        'market_reason': safe_text(market_state.get('market_reason')),
        'current_price': round(price, 3) if not math.isnan(price) else '',
        'risk_stop': round(stop, 3) if not math.isnan(stop) else '',
        'plan_level': safe_text(plan.get('plan_level')),
        'final_action': safe_text(row.get('final_action')),
        'warnings': warnings,
        'research_next_step': next_step(decision, failed, expectation_gap, valuation_level),
    }


def next_step(decision: str, failed: list[str], expectation_gap: str, valuation_level: str) -> str:
    if decision == 'BUY_READY':
        return '进入现实模拟盘，后续跟踪30/60/90天表现、公告证据和财务共振。'
    if '数据质量闸门未过' in failed:
        return '先修复数据源、字段缺失或股票级数据异常，不允许升级交易候选。'
    if '财务三表增强闸门未过' in failed:
        return '补齐利润表、资产负债表、现金流量表，复核应收、存货、负债和现金流/利润比。'
    if '估值/预期差闸门未过' in failed:
        return f'等待估值回落或业绩上修，当前为{valuation_level}/{expectation_gap}。'
    if '交易机会闸门未过' in failed:
        return '基本面可跟踪，但等待V2.1交易分重新站上买入阈值。'
    if '产业证据/最终研究闸门未过' in failed:
        return '补公告PDF、客户认证、订单、招投标、问询函等证据。'
    if '财务/基本面闸门未过' in failed:
        return '等待下一期财报验证营收、利润、经营现金流。'
    return '保留观察。'


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    date_key = args.date
    fundamental = read_csv(Path(args.fundamental_pool_dir) / 'current_learning_pool.csv')
    if fundamental.empty:
        raise SystemExit('missing current_learning_pool.csv')
    final_score = read_csv(Path(args.evidence_dir) / f'final_score_{date_key}.csv')
    evidence = read_csv(Path(args.evidence_dir) / f'evidence_quality_{date_key}.csv')
    financial = read_csv(Path(args.evidence_dir) / f'financial_quality_{date_key}.csv')
    valuation = read_csv(Path(args.valuation_dir) / f'valuation_score_{date_key}.csv')
    financial_statements = read_csv(Path(args.financial_statements_dir) / 'current_financial_statement_scores.csv')
    data_quality_stock = read_csv(Path(args.data_quality_dir) / 'current_data_quality_stock.csv')
    data_quality_state = load_data_quality_state(Path(args.data_quality_dir))
    final_plan = read_json(Path(args.final_layer_dir) / 'current_final_trade_plan.json') or []
    trade_scores, trade_source = load_trade_scores(Path(args.historical_backtest_dir), date_key)
    market_state = load_market_state(Path(args.historical_backtest_dir), date_key)
    messages = [
        "trade_score_source=" + str(trade_source),
        "market_reason=" + safe_text(market_state.get("market_reason")),
        "data_quality_status=" + safe_text(data_quality_state.get("status")) + "/" + str(data_quality_state.get("overall_score")),
    ]
    for frame in [fundamental, final_score, evidence, financial, valuation, financial_statements, data_quality_stock]:
        if not frame.empty and 'code' in frame.columns:
            frame['code'] = frame['code'].map(normalize_code)
    merged = fundamental.copy()
    merged['code'] = merged['code'].map(normalize_code)
    for frame, prefix in [
        (final_score, 'final_'),
        (evidence, 'evidence_'),
        (financial, 'fin_'),
        (valuation, 'val_'),
        (financial_statements, 'fs_'),
        (data_quality_stock, 'dq_'),
    ]:
        merged = merged.merge(add_prefix(frame, prefix), on='code', how='left')
    plan_by_code = {normalize_code(item.get('code')): item for item in final_plan if isinstance(item, dict)}
    rows = []
    for _, row in merged.iterrows():
        item = row.to_dict()
        code = normalize_code(item.get('code'))
        item['plan'] = plan_by_code.get(code, {})
        item['trade'] = trade_scores.get(code, {})
        rows.append(classify(pd.Series(item), market_state, data_quality_state, args))
    rows = sorted(rows, key=lambda item: item['fundamental_first_score'], reverse=True)[:args.max_candidates]
    return rows, market_state, messages


def write_report(path: Path, rows: list[dict[str, Any]], market_state: dict[str, Any], messages: list[str]) -> None:
    lines = [
        f'# 基本面优先闸门 V2.2 - {datetime.now():%Y-%m-%d}',
        '',
        '定位：先过数据质量、财务三表、财务质量、产业证据、估值预期差，再看V2.1交易机会。只有全部通过才进入现实模拟盘候选。',
        '',
        "- 市场风控：" + safe_text(market_state.get("market_reason")),
        '',
        '## 候选结果',
        '| 排名 | 代码 | 名称 | 结论 | 总分 | 公司质量 | 三表 | 数据质量 | 产业证据 | 估值预期差 | 交易机会 | 未过闸门 |',
        '|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|',
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append("| {idx} | {code} | {name} | {decision} | {total:.2f} | {company:.2f} | {stmt} | {dq:.2f} | {industry:.2f} | {value:.2f} | {opportunity:.2f} | {failed} |".format(idx=idx, code=row["code"], name=row["name"], decision=row["decision"], total=row["fundamental_first_score"], company=row["company_quality_score"], stmt=row.get("financial_statement_score", ""), dq=safe_float(row.get("data_quality_score"), 0), industry=row["industry_logic_score"], value=row["value_gap_score"], opportunity=row["opportunity_score"], failed=row["failed_gates"]))
    lines += ['', '## 数据提醒']
    lines.extend(f'- {message}' for message in messages)
    path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    rows, market_state, messages = build(args)
    date_key = args.date
    df = pd.DataFrame(rows)
    for path in [output_dir / f'fundamental_first_candidates_{date_key}.csv', output_dir / 'current_fundamental_first_candidates.csv']:
        df.to_csv(path, index=False, encoding='utf-8-sig')
    for path in [output_dir / f'fundamental_first_candidates_{date_key}.json', output_dir / 'current_fundamental_first_candidates.json']:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_report(report_dir / f'fundamental_first_{date_key}.md', rows, market_state, messages)
    print(json.dumps({'ok': True, 'rows': len(rows), 'buy_ready': sum(1 for row in rows if row['decision'] == 'BUY_READY')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
