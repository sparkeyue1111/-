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
    parser.add_argument('--candidate-threshold', type=float, default=70.0)
    parser.add_argument('--research-queue-threshold', type=float, default=70.0)
    parser.add_argument('--watch-threshold', type=float, default=58.0)
    parser.add_argument('--soft-evidence-score', type=float, default=45.0)
    parser.add_argument('--soft-expectation-gap-score', type=float, default=50.0)
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


def rebase_trade_row(row: pd.Series, current_price: float) -> pd.Series:
    """Refresh weekly proxy metrics with the latest closing price.

    The full-market backtest is intentionally weekly because it is expensive.
    For the live strategy pool we can recover each horizon's reference price
    from the previous return and revalue it with today's close. Volatility and
    liquidity stay on the latest full-market snapshot.
    """
    old_close = safe_float(row.get('close'))
    if math.isnan(current_price) or current_price <= 0 or math.isnan(old_close) or old_close <= 0:
        return row

    refreshed = row.copy()
    refreshed['close'] = current_price
    for field in ('ret20', 'ret60', 'ret120'):
        old_return = safe_float(row.get(field))
        if math.isnan(old_return) or old_return <= -99.0:
            continue
        reference_close = old_close / (1.0 + old_return / 100.0)
        if reference_close > 0:
            refreshed[field] = (current_price / reference_close - 1.0) * 100.0

    old_drawdown = safe_float(row.get('drawdown120'))
    if not math.isnan(old_drawdown) and old_drawdown > -99.0:
        high120 = old_close / (1.0 + old_drawdown / 100.0)
        if high120 > 0:
            refreshed['drawdown120'] = min(0.0, (current_price / high120 - 1.0) * 100.0)
    return refreshed


def load_trade_scores(
    backtest_dir: Path,
    date_key: str,
    current_prices: dict[str, float] | None = None,
) -> tuple[dict[str, dict[str, Any]], str]:
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
        current_price = safe_float((current_prices or {}).get(code))
        base_date = row['date_dt']
        trade_row = rebase_trade_row(row, current_price)
        old_close = safe_float(row.get('close'))
        refreshed = (
            not math.isnan(current_price)
            and current_price > 0
            and not math.isnan(old_close)
            and old_close > 0
        )
        result[code] = {
            'trade_score': adjusted_score_v21(trade_row),
            'trade_score_date': (
                pd.to_datetime(date_key, format='%Y%m%d').strftime('%Y-%m-%d')
                if refreshed
                else (base_date.strftime('%Y-%m-%d') if not pd.isna(base_date) else '')
            ),
            'trade_score_source': f'daily_close_overlay:{path.name}' if refreshed else path.name,
            'ret20': safe_float(trade_row.get('ret20')),
            'ret60': safe_float(trade_row.get('ret60')),
            'ret120': safe_float(trade_row.get('ret120')),
            'vol60': safe_float(trade_row.get('vol60')),
            'drawdown120': safe_float(trade_row.get('drawdown120')),
            'close': safe_float(trade_row.get('close')),
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
    keys = [
        '经营现金流为负',
        '退市',
        '处罚',
        '立案',
        '无法表示',
        '保留意见',
        '三表增强层质量偏弱',
        '经营现金流/利润偏低',
    ]
    return any(key in text for key in keys)


SERENITY_CHAIN_RULES = [
    (r"中际旭创|新易盛|天孚通信|光模块|CPO|光通信|高速光|800G|1\.6T", "AI算力光互连/光模块", "高速带宽、低功耗互连和客户认证周期"),
    (r"海光信息|寒武纪|景嘉微|芯片|GPU|CPU|算力|处理器|AI服务器", "AI算力芯片/服务器", "国产算力供给、生态适配和客户导入"),
    (r"北方华创|中微公司|华海清科|拓荆科技|芯源微|半导体设备|刻蚀|薄膜|CMP|量测|清洗|先进封装", "半导体设备/先进制造", "设备验证、工艺稳定性、国产替代和扩产节奏"),
    (r"沪电股份|深南电路|生益科技|胜宏科技|PCB|CCL|覆铜板|服务器板|高速板", "AI服务器PCB/材料", "高速材料、良率、认证和产能爬坡"),
    (r"宁德时代|电池|储能|锂|正极|负极|隔膜|电解液", "动力电池/储能链", "成本、安全性、客户定点和产能利用率"),
    (r"中国船舶|中船|船舶|军工|发动机|雷达|导弹|航空", "高端制造/军工船舶", "长周期订单、交付能力、配套供应链和现金流兑现"),
    (r"证券|券商|银行|保险|金融", "金融服务/资本市场链条", "政策、市场成交活跃度和风险偏好传导，不是硬供应链卡点"),
    (r"茅台|五粮液|泸州老窖|白酒|消费|食品|饮料", "消费品牌/渠道链条", "品牌定价权、渠道库存、动销和现金回款"),
    (r"药|医疗|生物|CXO|创新药|器械", "医药制造/医疗服务链", "临床进展、商业化、医保支付和客户验证"),
]


def extract_count(text: str, pattern: str) -> int:
    match = re.search(pattern, safe_text(text))
    return int(match.group(1)) if match else 0


def serenity_chain(row: pd.Series, name: str) -> tuple[str, str, str]:
    pieces = [
        name,
        safe_text(row.get('lane')),
        safe_text(row.get('pool_type')),
        safe_text(row.get('fundamental_notes')),
        safe_text(row.get('evidence_evidence_notes')),
        safe_text(row.get('final_reason')),
        safe_text(row.get('val_expectation_gap_reason')),
    ]
    combined = '；'.join(part for part in pieces if part)
    for pattern, chain_position, bottleneck in SERENITY_CHAIN_RULES:
        if re.search(pattern, combined, flags=re.IGNORECASE):
            return chain_position, bottleneck, '已按公司名称/公告关键词初步识别'
    return '待确认产业链层级', '暂无明确稀缺层，需补行业位置和客户穿透证据', '未识别到稳定产业链关键词'


def serenity_evidence_grade(
    evidence_score: float,
    research_score: float,
    evidence_notes: str,
    evidence_warnings: str,
) -> str:
    pdf_count = extract_count(evidence_notes, r'PDF全文解析(\d+)份')
    project_count = extract_count(evidence_notes, r'项目/订单/客户线索(\d+)条')
    if evidence_score >= 85 and research_score >= 70 and pdf_count > 0 and 'PDF 正文未完成解析' not in evidence_warnings:
        return '强：有公告/PDF正文或硬材料支撑'
    if evidence_score >= 70 or project_count > 0:
        return '中：有公开公告/互动易线索，但仍需穿透PDF正文和收入映射'
    if evidence_score >= 45:
        return '弱：只有初步公开线索，证据链不够闭环'
    return '待验证：缺少足够公开证据'


def serenity_customer_evidence(evidence_notes: str, evidence_warnings: str) -> str:
    project_count = extract_count(evidence_notes, r'项目/订单/客户线索(\d+)条')
    ir_count = extract_count(evidence_notes, r'互动易/投资者问答(\d+)条')
    pdf_count = extract_count(evidence_notes, r'PDF全文解析(\d+)份')
    parts: list[str] = []
    if project_count > 0:
        parts.append(f'标题/元数据层发现{project_count}条项目、订单、客户或认证线索')
    if ir_count > 0:
        parts.append(f'互动易/投资者问答有{ir_count}条可追踪线索')
    if pdf_count > 0:
        parts.append(f'已有{pdf_count}份PDF正文解析可继续核验')
    if not parts:
        parts.append('尚未发现明确客户、认证或订单线索')
    if 'PDF 正文未完成解析' in evidence_warnings or pdf_count == 0:
        parts.append('PDF正文未形成闭环，客户/订单真实性和收入占比需要人工抽查')
    return '；'.join(parts)


def serenity_financial_evidence(row: pd.Series, financial_score: float, metric_count: int) -> str:
    notes = safe_text(row.get('fin_financial_notes')) or safe_text(row.get('fundamental_notes'))
    warnings = safe_text(row.get('fin_financial_warnings')) or safe_text(row.get('fundamental_warnings'))
    if not notes:
        notes = '财务摘要不足，需补三表细项'
    quality = '较好' if financial_score >= 75 else ('一般' if financial_score >= 60 else '偏弱')
    return f"财务质量{quality}，可用指标{metric_count}项；{notes}；{warnings or '暂无硬性财务红旗'}"


def serenity_valuation_text(valuation_level: str, expectation_gap: str, valuation_warning: str) -> str:
    level = valuation_level or '估值未确认'
    gap = expectation_gap or '预期差未确认'
    warning = valuation_warning or '暂无估值红旗'
    if level in {'估值偏高', '估值极高'} or gap in {'成长强但估值透支', '低质量+高估值风险', '低质量/高估值风险'}:
        return f'可能透支：{level}/{gap}；{warning}'
    if level in {'估值有保护', '估值偏低'}:
        return f'暂有安全边际：{level}/{gap}；{warning}'
    return f'中性或待确认：{level}/{gap}；{warning}'


def serenity_positioning_verdict(
    chain_position: str,
    evidence_grade: str,
    evidence_score: float,
    financial_score: float,
    customer_evidence: str,
) -> str:
    if '金融服务' in chain_position:
        return '不按硬供应链卡点理解，更多是市场活跃度和政策周期传导，需降低卡点评级。'
    if chain_position.startswith('待确认'):
        return '暂不能证明真卡位，需先确认产业链层级、客户认证和收入结构。'
    if '强' in evidence_grade and financial_score >= 70:
        return '公开证据和财务质量共同支持卡位判断，但仍需持续核对客户/订单转收入。'
    if '中' in evidence_grade and evidence_score >= 70:
        return '有卡位线索，但主要仍在公告标题/互动易层，需要PDF正文、客户认证或订单金额继续加固。'
    if '尚未发现明确' in customer_evidence:
        return '产业链位置可初步识别，但缺少客户/认证/订单证据，不能视为真卡位。'
    return '卡位判断偏弱，当前更适合放入研究队列而不是直接交易。'


def serenity_downgrade_text(row: pd.Series, failed: list[str], warnings: str, trade_score: float) -> str:
    rules: list[str] = []
    downgrade_rule = safe_text(row.get('val_downgrade_rule'))
    if downgrade_rule:
        rules.append(downgrade_rule)
    if failed:
        rules.append('未过闸门未改善：' + '、'.join(failed))
    if 'PDF 正文未完成解析' in warnings:
        rules.append('公告/PDF抽查后不能证明客户、订单或卡点收入')
    rules.append('经营现金流转弱、应收/存货恶化或毛利率不能体现稀缺性')
    if trade_score:
        rules.append('交易分跌破持有阈值或价格触发系统止损')
    return '；'.join(dict.fromkeys(rule for rule in rules if rule))


def serenity_opportunity_text(decision: str, failed: list[str], total_score: float, trade_score: float) -> str:
    if decision == 'BUY_READY':
        return f'进入：基本面、证据、估值、交易和市场风控全部通过；总分{total_score:.1f}，交易分{trade_score:.1f}，下一交易日仍满足才进入模拟盘。'
    if decision in {'WATCH', 'TRADE_CANDIDATE'}:
        reason = '；'.join(failed) if failed else '仍需等待更好的估值或交易结构'
        return f'暂不严格买入：{reason}。保留观察，等待证据、估值或交易分共振。'
    if decision in {'PENDING_RESEARCH', 'RESEARCH_QUEUE', 'FUNDAMENTAL_POOL'}:
        return '不进入交易机会层：基本面线索存在，但尚未完成公告、证据、估值和AI深研闭环。'
    reason = '；'.join(failed) if failed else '系统闸门未形成共振'
    return f'不进入交易机会层：{reason}。'


def build_serenity_layer(
    row: pd.Series,
    *,
    name: str,
    decision: str,
    failed: list[str],
    financial_score: float,
    evidence_score: float,
    research_score: float,
    value_score: float,
    trade_score: float,
    total_score: float,
    metric_count: int,
    valuation_level: str,
    expectation_gap: str,
    warnings: str,
) -> dict[str, Any]:
    evidence_notes = safe_text(row.get('evidence_evidence_notes'))
    evidence_warnings = safe_text(row.get('evidence_evidence_warnings'))
    chain_position, bottleneck_layer, chain_note = serenity_chain(row, name)
    evidence_grade = serenity_evidence_grade(evidence_score, research_score, evidence_notes, evidence_warnings)
    customer_evidence = serenity_customer_evidence(evidence_notes, evidence_warnings)
    financial_evidence = serenity_financial_evidence(row, financial_score, metric_count)
    valuation_stretch = serenity_valuation_text(
        valuation_level,
        expectation_gap,
        safe_text(row.get('val_valuation_warning')),
    )
    positioning_verdict = serenity_positioning_verdict(
        chain_position,
        evidence_grade,
        evidence_score,
        financial_score,
        customer_evidence,
    )
    downgrade_conditions = serenity_downgrade_text(row, failed, warnings, trade_score)
    opportunity_rationale = serenity_opportunity_text(decision, failed, total_score, trade_score)
    chain_score = 80.0 if not chain_position.startswith('待确认') and '金融服务' not in chain_position else (45.0 if '金融服务' in chain_position else 35.0)
    serenity_score = clamp(
        evidence_score * 0.28
        + financial_score * 0.22
        + research_score * 0.18
        + value_score * 0.16
        + trade_score * 0.08
        + chain_score * 0.08
    )
    return {
        'serenity_research_score': round(serenity_score, 2),
        'serenity_chain_position': chain_position,
        'serenity_bottleneck_layer': bottleneck_layer,
        'serenity_chain_note': chain_note,
        'serenity_positioning_verdict': positioning_verdict,
        'serenity_evidence_grade': evidence_grade,
        'serenity_customer_order_evidence': customer_evidence,
        'serenity_financial_evidence': financial_evidence,
        'serenity_valuation_stretch': valuation_stretch,
        'serenity_downgrade_conditions': downgrade_conditions,
        'serenity_opportunity_rationale': opportunity_rationale,
    }


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
    hard_warning_text = '；'.join([
        safe_text(row.get('fundamental_warnings')),
        safe_text(row.get('fin_financial_warnings')),
        safe_text(row.get('fs_financial_statement_warnings')),
        safe_text(row.get('evidence_evidence_warnings')),
        data_quality_warnings,
    ])
    warnings = '；'.join([
        hard_warning_text,
        safe_text(row.get('val_valuation_warning')),
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
    final_layer_downgraded = (
        safe_text(plan.get('plan_level')) == '降级'
        or safe_text(plan.get('action')) == '降级'
        or safe_text(row.get('final_action')) == '降级'
    )
    red_flag = has_red_flag(hard_warning_text) or final_layer_downgraded

    valuation_hard_block = valuation_level in {'估值极高'} or expectation_gap in {'低质量+高估值风险'}
    soft_evidence_gate = has_deep_research and (evidence_score >= args.soft_evidence_score or research_score >= 50)
    soft_valuation_gate = expectation_score >= args.soft_expectation_gap_score and not valuation_hard_block
    trade_candidate_gate = trade_score >= args.candidate_threshold
    research_queue_gate = (
        trade_score >= args.research_queue_threshold
        or company_score >= 88
        or total_score >= 52
    )

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
        if trade_candidate_gate and market_ok and company_score >= 88:
            decision = 'TRADE_CANDIDATE'
            action = '基本面和交易结构进入候选样本，需优先补公告证据、估值和AI报告'
            failed = ['尚未覆盖公告/证据/估值深研；交易候选为研究样本，不是严格买入信号']
        elif research_queue_gate:
            decision = 'RESEARCH_QUEUE'
            action = '基本面较好但尚未深研，优先补公告证据、估值和AI报告'
            failed = ['尚未覆盖公告/证据/估值深研，不代表研究失败']
        else:
            decision = 'FUNDAMENTAL_POOL'
            action = '保留在基本面池，等待轮动进入深度研究'
            failed = ['尚未覆盖公告/证据/估值深研，不代表研究失败']
    elif not red_flag and data_quality_gate and statement_gate and fundamental_gate and evidence_gate and valuation_gate and trade_gate and market_ok:
        decision = 'BUY_READY'
        action = '严格闸门全部通过，进入现实模拟盘买入候选'
    elif (
        not red_flag
        and data_quality_gate
        and statement_gate
        and fundamental_gate
        and market_ok
        and soft_evidence_gate
        and soft_valuation_gate
        and trade_candidate_gate
    ):
        decision = 'TRADE_CANDIDATE'
        action = '进入交易机会层观察样本，尚未达到严格买入闸门'
    elif (
        not red_flag
        and data_quality_gate
        and statement_gate
        and fundamental_gate
        and has_deep_research
        and total_score >= args.watch_threshold
    ):
        decision = 'WATCH'
        action = '已深研但估值、证据或交易条件未完全成熟，继续观察'
    elif not red_flag and fundamental_gate and (not statement_gate or not data_quality_gate):
        decision = 'RESEARCH_QUEUE'
        action = '基本面有线索，但需补齐数据质量或三表验证'
    elif not has_deep_research and not red_flag and company_score >= 70:
        decision = 'FUNDAMENTAL_POOL'
        action = '保留在基本面池，暂不升级深研'
    else:
        decision = 'REJECT'
        action = '已研究或硬条件不足，不进入交易机会层'
    price = coalesce_float(row.get('price'), plan.get('snapshot_price'), trade.get('close'), default=math.nan)
    stop = coalesce_float(plan.get('risk_stop'), price * 0.88 if not math.isnan(price) else math.nan, default=math.nan)
    serenity = build_serenity_layer(
        row,
        name=name,
        decision=decision,
        failed=failed,
        financial_score=financial_score,
        evidence_score=evidence_score,
        research_score=research_score,
        value_score=value_score,
        trade_score=trade_score,
        total_score=total_score,
        metric_count=metric_count,
        valuation_level=valuation_level,
        expectation_gap=expectation_gap,
        warnings=warnings,
    )
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
        'trade_score_source': safe_text(trade.get('trade_score_source')),
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
        **serenity,
        'research_next_step': next_step(decision, failed, expectation_gap, valuation_level),
    }


def next_step(decision: str, failed: list[str], expectation_gap: str, valuation_level: str) -> str:
    if decision == 'BUY_READY':
        return '进入严格现实模拟盘，后续跟踪30/60/90天表现、公告证据和财务共振。'
    if decision == 'TRADE_CANDIDATE':
        return '进入交易机会层样本；继续确认估值、证据和下一交易日结构，暂不按严格盘买入。'
    if decision == 'RESEARCH_QUEUE':
        return '优先补公告PDF、客户认证、订单、问询函、估值和AI单票报告。'
    if decision == 'FUNDAMENTAL_POOL':
        return '保留在基本面池，等待轮动深研或交易分/公司质量继续抬升。'
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
    current_prices = {
        normalize_code(row.get('code')): safe_float(row.get('price'))
        for _, row in fundamental.iterrows()
    }
    trade_scores, trade_source = load_trade_scores(
        Path(args.historical_backtest_dir),
        date_key,
        current_prices=current_prices,
    )
    market_state = load_market_state(Path(args.historical_backtest_dir), date_key)
    messages = [
        "trade_score_source=" + str(trade_source),
        "trade_score_mode=daily_close_overlay",
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
        '定位：先过数据质量、财务三表、财务质量、产业证据、估值预期差，再看V2.1交易机会。BUY_READY 才进入严格模拟盘；TRADE_CANDIDATE 用于交易机会层观察；RESEARCH_QUEUE 表示优先补深研。',
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
    print(json.dumps({
        'ok': True,
        'rows': len(rows),
        'buy_ready': sum(1 for row in rows if row['decision'] == 'BUY_READY'),
        'trade_candidate': sum(1 for row in rows if row['decision'] == 'TRADE_CANDIDATE'),
        'research_queue': sum(1 for row in rows if row['decision'] == 'RESEARCH_QUEUE'),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
