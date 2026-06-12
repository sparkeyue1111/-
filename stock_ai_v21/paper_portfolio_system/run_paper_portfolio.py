from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='run fundamental first paper portfolio')
    parser.add_argument('--candidate-dir', default='/app/data/fundamental_first')
    parser.add_argument('--portfolio-dir', default='/app/data/paper_portfolio')
    parser.add_argument('--report-dir', default='/app/reports')
    parser.add_argument('--date', default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument('--initial-capital', type=float, default=100000.0)
    parser.add_argument('--max-positions', type=int, default=5)
    parser.add_argument('--max-position-pct', type=float, default=0.20)
    parser.add_argument('--entry-threshold', type=float, default=76.0)
    parser.add_argument('--hold-threshold', type=float, default=68.0)
    parser.add_argument('--commission-rate', type=float, default=0.0003)
    parser.add_argument('--stamp-duty-rate', type=float, default=0.0005)
    parser.add_argument('--lot-size', type=int, default=100)
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


def normalize_code(value: Any) -> str:
    text = safe_text(value)
    digits = ''.join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else text.zfill(6)[-6:]


def read_candidates(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={'code': str}, encoding='utf-8-sig')
    if 'code' in df.columns:
        df['code'] = df['code'].map(normalize_code)
    return df


def load_state(path: Path, initial_capital: float) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'cash': initial_capital, 'positions': [], 'trades': [], 'initial_capital': initial_capital}


def position_value(position: dict[str, Any]) -> float:
    return safe_float(position.get('shares'), 0.0) * safe_float(position.get('last_price'), 0.0)


def equity_of(state: dict[str, Any]) -> float:
    return safe_float(state.get('cash'), 0.0) + sum(position_value(position) for position in state.get('positions', []))


def as_row_map(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if df.empty:
        return {}
    return {normalize_code(row.get('code')): row.to_dict() for _, row in df.iterrows()}


def sell_position(state: dict[str, Any], position: dict[str, Any], price: float, reason: str, date_key: str, args: argparse.Namespace) -> dict[str, Any]:
    shares = int(safe_float(position.get('shares'), 0.0))
    gross = shares * price
    fee = gross * (args.commission_rate + args.stamp_duty_rate)
    net = gross - fee
    cost = safe_float(position.get('cost'), 0.0)
    pnl = net - cost
    state['cash'] = safe_float(state.get('cash'), 0.0) + net
    trade = {
        'date': date_key,
        'side': 'SELL',
        'code': normalize_code(position.get('code')),
        'name': safe_text(position.get('name')),
        'price': round(price, 3),
        'shares': shares,
        'amount': round(net, 2),
        'fee': round(fee, 2),
        'pnl': round(pnl, 2),
        'reason': reason,
    }
    state.setdefault('trades', []).append(trade)
    return trade


def buy_candidate(state: dict[str, Any], row: dict[str, Any], equity: float, date_key: str, args: argparse.Namespace) -> dict[str, Any] | None:
    price = safe_float(row.get('current_price'))
    if math.isnan(price) or price <= 0:
        return None
    cash = safe_float(state.get('cash'), 0.0)
    budget = min(cash, equity * args.max_position_pct)
    shares = int(budget // (price * args.lot_size)) * args.lot_size
    if shares <= 0:
        return None
    gross = shares * price
    fee = gross * args.commission_rate
    total = gross + fee
    if total > cash:
        return None
    state['cash'] = cash - total
    position = {
        'code': normalize_code(row.get('code')),
        'name': safe_text(row.get('name')),
        'entry_date': date_key,
        'entry_price': round(price, 3),
        'last_price': round(price, 3),
        'shares': shares,
        'cost': round(total, 2),
        'risk_stop': safe_float(row.get('risk_stop'), price * 0.88),
        'last_trade_score': safe_float(row.get('trade_score'), 0.0),
        'fundamental_first_score': safe_float(row.get('fundamental_first_score'), 0.0),
    }
    state.setdefault('positions', []).append(position)
    trade = {
        'date': date_key,
        'side': 'BUY',
        'code': position['code'],
        'name': position['name'],
        'price': round(price, 3),
        'shares': shares,
        'amount': round(total, 2),
        'fee': round(fee, 2),
        'pnl': 0.0,
        'reason': 'BUY_READY进入现实模拟盘',
    }
    state.setdefault('trades', []).append(trade)
    return trade


def run(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate_path = Path(args.candidate_dir) / 'current_fundamental_first_candidates.csv'
    candidates = read_candidates(candidate_path)
    rows_by_code = as_row_map(candidates)
    portfolio_dir = Path(args.portfolio_dir)
    state_path = portfolio_dir / 'paper_portfolio_state.json'
    state = load_state(state_path, args.initial_capital)
    state.setdefault('pending_orders', [])
    trades_today: list[dict[str, Any]] = []

    new_positions = []
    for position in state.get('positions', []):
        code = normalize_code(position.get('code'))
        row = rows_by_code.get(code, {})
        price = safe_float(row.get('current_price'), safe_float(position.get('last_price'), safe_float(position.get('entry_price'), 0.0)))
        position['last_price'] = round(price, 3)
        position['last_trade_score'] = safe_float(row.get('trade_score'), safe_float(position.get('last_trade_score'), 0.0))
        stop = safe_float(row.get('risk_stop'), safe_float(position.get('risk_stop'), price * 0.88))
        position['risk_stop'] = round(stop, 3)
        decision = safe_text(row.get('decision'))
        reason = ''
        if price <= stop:
            reason = '触发风险止损'
        elif decision == 'REJECT':
            reason = '基本面闸门降级为REJECT'
        elif decision and position['last_trade_score'] < args.hold_threshold:
            reason = '交易分低于持有阈值'
        if reason:
            trades_today.append(sell_position(state, position, price, reason, args.date, args))
        else:
            new_positions.append(position)
    state['positions'] = new_positions

    held_codes = {normalize_code(position.get('code')) for position in state.get('positions', [])}

    # 昨日或更早的 BUY_READY 信号，必须在下一次运行时仍满足条件才成交，避免收盘后信号当天成交。
    remaining_pending: list[dict[str, Any]] = []
    for order in state.get('pending_orders', []):
        code = normalize_code(order.get('code'))
        if code in held_codes:
            continue
        row = rows_by_code.get(code)
        if not row:
            continue
        if safe_text(order.get('signal_date')) == args.date:
            remaining_pending.append(order)
            continue
        decision = safe_text(row.get('decision'))
        trade_score = safe_float(row.get('trade_score'), safe_float(order.get('trade_score'), 0.0))
        if decision == 'BUY_READY' and trade_score >= args.entry_threshold and len(state.get('positions', [])) < args.max_positions:
            trade = buy_candidate(state, row, equity_of(state), args.date, args)
            if trade:
                trades_today.append(trade)
                held_codes.add(code)
                continue
        elif decision in {'WATCH', 'PENDING_RESEARCH', 'RESEARCH_QUEUE', 'FUNDAMENTAL_POOL', 'TRADE_CANDIDATE'} and trade_score >= args.hold_threshold:
            remaining_pending.append(order)
    state['pending_orders'] = remaining_pending
    pending_codes = {normalize_code(order.get('code')) for order in state.get('pending_orders', [])}

    if not candidates.empty:
        buys = candidates[candidates['decision'] == 'BUY_READY'].copy()
        buys['sort_score'] = buys['fundamental_first_score'].map(lambda value: safe_float(value, 0.0)) * 0.7 + buys['trade_score'].map(lambda value: safe_float(value, 0.0)) * 0.3
        buys = buys.sort_values('sort_score', ascending=False)
        for _, row in buys.iterrows():
            if len(state.get('positions', [])) >= args.max_positions:
                break
            code = normalize_code(row.get('code'))
            if code in held_codes or code in pending_codes:
                continue
            if safe_float(row.get('trade_score'), 0.0) < args.entry_threshold:
                continue
            state.setdefault('pending_orders', []).append({
                'signal_date': args.date,
                'code': code,
                'name': safe_text(row.get('name')),
                'signal_price': safe_float(row.get('current_price')),
                'trade_score': safe_float(row.get('trade_score'), 0.0),
                'fundamental_first_score': safe_float(row.get('fundamental_first_score'), 0.0),
            })
            pending_codes.add(code)
    state['last_update'] = args.date
    state['equity'] = round(equity_of(state), 2)
    return state, trades_today

def write_outputs(args: argparse.Namespace, state: dict[str, Any], trades_today: list[dict[str, Any]]) -> None:
    portfolio_dir = Path(args.portfolio_dir)
    report_dir = Path(args.report_dir)
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    state_path = portfolio_dir / 'paper_portfolio_state.json'
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    holdings = []
    for position in state.get('positions', []):
        market_value = position_value(position)
        cost = safe_float(position.get('cost'), 0.0)
        pnl = market_value - cost
        holdings.append({**position, 'market_value': round(market_value, 2), 'unrealized_pnl': round(pnl, 2), 'unrealized_return_pct': round(pnl / cost * 100, 2) if cost > 0 else 0.0})
    pd.DataFrame(holdings).to_csv(portfolio_dir / 'current_paper_holdings.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(trades_today).to_csv(portfolio_dir / f'paper_trades_{args.date}.csv', index=False, encoding='utf-8-sig')
    curve_path = portfolio_dir / 'paper_equity_curve.csv'
    curve_row = pd.DataFrame([{'date': args.date, 'cash': round(safe_float(state.get('cash'), 0.0), 2), 'equity': round(safe_float(state.get('equity'), 0.0), 2), 'positions': len(state.get('positions', []))}])
    if curve_path.exists():
        curve = pd.read_csv(curve_path, dtype={'date': str})
        curve = curve[curve['date'] != args.date]
        curve = pd.concat([curve, curve_row], ignore_index=True).sort_values('date')
    else:
        curve = curve_row
    curve.to_csv(curve_path, index=False, encoding='utf-8-sig')
    lines = ["# 现实模拟盘 - " + str(args.date), "", "- 初始资金：{:.2f}".format(safe_float(state.get("initial_capital"), args.initial_capital)), "- 当前权益：{:.2f}".format(safe_float(state.get("equity"), 0.0)), "- 现金：{:.2f}".format(safe_float(state.get("cash"), 0.0)), "- 持仓数：{}".format(len(state.get("positions", []))), "- 今日交易：{}".format(len(trades_today)), "- 待成交信号：{}".format(len(state.get("pending_orders", [])))]
    (report_dir / f'paper_portfolio_{args.date}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    args = parse_args()
    state, trades_today = run(args)
    write_outputs(args, state, trades_today)
    print(json.dumps({'ok': True, 'equity': state.get('equity'), 'positions': len(state.get('positions', [])), 'trades_today': len(trades_today)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
