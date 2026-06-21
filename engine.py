"""
Prop-firm profit-farming simulation engine — pure functions, no I/O.

Replays a strategy's *real* historical trades against typical prop-firm
evaluation + funded rules and estimates the economics of "farming" payouts
across many staggered accounts over 12 / 18 / 24-month horizons.

The drawdown + consistency math mirrors the live trading system's prop_guard /
firm_rules modules so the simulator and the real bot agree on what "blown" and
"consistent" mean.

MODEL (transparent + tweakable — these are documented assumptions, not gospel):
  * Trades are collapsed into a per-trading-day P&L series ($ per day).
  * A "trader" starts on a given calendar day and runs the strategy forward
    until the period ends. New traders start every day / week / month — the
    staircase that models scaling the operation up over time.
  * AUDITION (the eval): accumulate daily P&L from a 0 baseline. PASS when net
    >= audition_target AND the consistency rule (if on) is satisfiable. BLOW
    when the balance touches the trailing drawdown line -> pay another challenge
    fee and start a fresh attempt, until the trader's runway runs out.
  * FUNDED: keep accumulating. Until +funded_dd_lock_profit the trailing
    drawdown still applies; after it, the account can only fail if the balance
    goes negative (the Apex/MFFU "locks at breakeven" mechanic). Every time the
    balance reaches payout_trigger, withdraw payout_amount and bank a payout,
    gated by min_payout_days qualifying days and a payout_delay_days approval
    wait. BLOW on a drawdown breach.
  * COSTS: challenge fee per audition attempt (one-time) or per active month
    (monthly billing); activation fee once on funding; VPS monthly while active.
    Per-account fees scale by num_accounts (each account is its own eval); VPS
    is one box for all accounts.
  * Net profit = total payouts - total costs.   ROI = net / costs.

Everything is deterministic (each trader just starts at a different offset into
the cycled trade history) so results are reproducible and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Dict

TRADING_DAYS_PER_MONTH = 21


# ── drawdown line (same semantics as prop_guard.py) ──────────────────────────
def drawdown_line(high_water: float, start: float, dd_amount: float,
                  dd_type: str = "eod", locks_at_breakeven: bool = True) -> float:
    """The balance level that, if touched, blows the account.

    static : fixed at start - dd_amount.
    eod / trailing : trails the high-water mark by dd_amount, and (if it
        locks at breakeven) never rises above the starting balance.
    """
    if dd_type == "static":
        return start - dd_amount
    raw = high_water - dd_amount
    return min(raw, start) if locks_at_breakeven else raw


# ── parameters ───────────────────────────────────────────────────────────────
@dataclass
class Params:
    # global
    num_accounts: int = 1
    size_multiplier: float = 1.0
    start_frequency: str = "daily"          # daily | weekly | monthly

    # audition / eval phase
    audition_start: float = 50000.0         # nominal eval account size (cosmetic baseline)
    audition_target: float = 3000.0
    audition_dd: float = 2000.0
    audition_dd_type: str = "eod"           # eod | trailing | static
    audition_dll: float = 0.0               # daily loss limit (0 = off)
    audition_consistency_pct: float = 50.0  # 0 = off
    challenge_fee: float = 91.0
    challenge_billing: str = "one-time"     # one-time | monthly

    # funded phase
    funded_start: float = 50000.0
    payout_trigger: float = 4000.0          # balance that unlocks a payout (profit terms below)
    payout_amount: float = 2000.0
    funded_dd: float = 2000.0
    funded_dd_type: str = "eod"
    funded_dll: float = 0.0
    funded_consistency_pct: float = 0.0     # 0 = off
    funded_dd_lock_profit: float = 2100.0   # after +this profit, only fail if balance < 0
    activation_fee: float = 0.0
    min_payout_days: int = 5                # qualifying days ($50+) before a payout
    min_payout_day_pnl: float = 50.0
    payout_delay_days: int = 5              # approval wait after each payout

    # costs
    use_vps: bool = True
    vps_monthly: float = 199.0


# ── per-account journey ──────────────────────────────────────────────────────
def simulate_account(stream: List[float], p: Params) -> Dict:
    """Walk one account through audition attempts then the funded phase using
    `stream` (the trader's forward daily-P&L runway, already size-scaled).

    Returns a per-account result dict.
    """
    n = len(stream)
    i = 0
    attempts = 0
    passed = False
    payouts = 0
    payout_cash = 0.0
    fees = 0.0
    months_active = 0.0

    # ---- audition: back-to-back attempts until pass or out of runway ----
    while i < n and not passed:
        attempts += 1
        if p.challenge_billing != "monthly":
            fees += p.challenge_fee
        start = p.audition_start
        hw = start
        cum = 0.0
        days = []
        while i < n:
            pnl = stream[i]
            i += 1
            if p.audition_dll and pnl < -p.audition_dll:
                pnl = -p.audition_dll            # daily-loss-limit caps the day
            cum += pnl
            days.append(pnl)
            bal = start + cum
            hw = max(hw, bal)
            line = drawdown_line(hw, start, p.audition_dd, p.audition_dd_type)
            if bal <= line:
                break                            # blown -> next attempt
            if cum >= p.audition_target and _consistency_ok(days, p.audition_consistency_pct):
                passed = True
                break

    # ---- funded: accumulate, bank payouts, until blown or runway ends ----
    if passed:
        fees += p.activation_fee
        start = p.funded_start
        hw = start
        cum = 0.0
        qualifying = 0
        while i < n:
            pnl = stream[i]
            i += 1
            if p.funded_dll and pnl < -p.funded_dll:
                pnl = -p.funded_dll
            cum += pnl
            bal = start + cum
            hw = max(hw, bal)
            if pnl >= p.min_payout_day_pnl:
                qualifying += 1
            # drawdown line: trails until +lock_profit, then floor at start (balance<0 fails)
            if cum >= p.funded_dd_lock_profit:
                line = start - start              # i.e. 0 absolute balance
            else:
                line = drawdown_line(hw, start, p.funded_dd, p.funded_dd_type)
            if bal <= line:
                break
            if bal - start >= _trigger_profit(p) and qualifying >= p.min_payout_days:
                payouts += 1
                payout_cash += p.payout_amount
                cum -= p.payout_amount
                qualifying = 0
                i += p.payout_delay_days          # approval wait, no trading

    # ---- VPS months: whole life of the account ----
    months_active = max(1.0, n / TRADING_DAYS_PER_MONTH)
    if p.challenge_billing == "monthly":
        fees += p.challenge_fee * months_active

    return {
        "attempts": attempts,
        "passed": int(passed),
        "funded": int(passed),
        "payouts": payouts,
        "payout_cash": round(payout_cash, 2),
        "fees": round(fees, 2),
        "months_active": round(months_active, 2),
    }


def _consistency_ok(day_pnls: List[float], pct: float) -> bool:
    """Best single day must be <= pct% of total positive profit. 0 = rule off."""
    if not pct:
        return True
    pos = sum(d for d in day_pnls if d > 0)
    if pos <= 0:
        return False
    best = max(day_pnls) if day_pnls else 0.0
    return (best / pos) * 100.0 <= pct


def _trigger_profit(p: Params) -> float:
    """Profit (above the funded baseline) that unlocks a payout."""
    return p.payout_trigger - p.funded_start


# ── daily series from trades ─────────────────────────────────────────────────
def daily_series(trade_pnls_by_day: Dict[str, float]) -> List[float]:
    """Ordered list of per-day P&L from a {date_string: pnl} mapping."""
    return [trade_pnls_by_day[d] for d in sorted(trade_pnls_by_day)]


def _trader_starts(total_days: int, frequency: str) -> List[int]:
    step = {"daily": 1, "weekly": 5, "monthly": TRADING_DAYS_PER_MONTH}.get(frequency, 1)
    return list(range(0, max(1, total_days), step))


# ── full simulation for one horizon ──────────────────────────────────────────
def simulate_period(series: List[float], months: int, p: Params) -> Dict:
    """Simulate every trader that starts within a `months`-long horizon and
    aggregate the economics. Returns averages + totals for the period."""
    if not series:
        return _empty_period(months)

    total_days = months * TRADING_DAYS_PER_MONTH
    starts = _trader_starts(total_days, p.start_frequency)
    sized = [x * p.size_multiplier for x in series]
    L = len(sized)

    trader_rows = []
    for s in starts:
        runway = total_days - s
        if runway <= 0:
            continue
        # forward stream = trade history cycled from this trader's offset
        stream = [sized[(s + j) % L] for j in range(runway)]
        acct = simulate_account(stream, p)

        # one trader runs num_accounts identical accounts in parallel
        n_acc = max(1, int(p.num_accounts))
        per_trader_payouts = acct["payouts"] * n_acc
        per_trader_cash = acct["payout_cash"] * n_acc
        # per-account fees scale by accounts; VPS is one box for the trader
        per_trader_fees = acct["fees"] * n_acc
        if p.use_vps:
            per_trader_fees += p.vps_monthly * acct["months_active"]
        net = per_trader_cash - per_trader_fees
        roi = (net / per_trader_fees * 100.0) if per_trader_fees > 0 else 0.0

        trader_rows.append({
            "attempts": acct["attempts"],
            "passed": acct["passed"],
            "payouts": per_trader_payouts,
            "payout_cash": per_trader_cash,
            "fees": per_trader_fees,
            "net": net,
            "roi": roi,
        })

    return _aggregate(months, trader_rows)


def _aggregate(months: int, rows: List[Dict]) -> Dict:
    n = len(rows)
    if n == 0:
        return _empty_period(months)
    passed = sum(r["passed"] for r in rows)

    def avg(key):
        return sum(r[key] for r in rows) / n

    return {
        "months": months,
        "total_traders": n,
        "avg_attempts": round(avg("attempts"), 2),
        "passed": passed,
        "pass_rate": round(100.0 * passed / n, 1),
        "avg_payouts": round(avg("payouts"), 2),
        "avg_payout_cash": round(avg("payout_cash"), 2),
        "avg_fees": round(avg("fees"), 2),
        "avg_net": round(avg("net"), 2),
        "avg_roi": round(avg("roi"), 1),
        "pct_positive_roi": round(100.0 * sum(1 for r in rows if r["net"] > 0) / n, 1),
        "traders": rows,
    }


def _empty_period(months: int) -> Dict:
    return {"months": months, "total_traders": 0, "avg_attempts": 0.0, "passed": 0,
            "pass_rate": 0.0, "avg_payouts": 0.0, "avg_payout_cash": 0.0,
            "avg_fees": 0.0, "avg_net": 0.0, "avg_roi": 0.0, "pct_positive_roi": 0.0,
            "traders": []}


def run(series: List[float], p: Params, horizons=(12, 18, 24)) -> Dict[int, Dict]:
    """Run every horizon. Returns {months: period_result}."""
    return {m: simulate_period(series, m, p) for m in horizons}


def monthly_projection(series: List[float], p: Params, months: int = 24) -> List[Dict]:
    """Cumulative net-profit walk for a single representative account (trader
    starting at day 0), reported month by month — for the projection chart."""
    total_days = months * TRADING_DAYS_PER_MONTH
    sized = [x * p.size_multiplier for x in series] or [0.0]
    L = len(sized)
    stream = [sized[j % L] for j in range(total_days)]

    rows, running = [], 0.0
    for m in range(1, months + 1):
        chunk = stream[(m - 1) * TRADING_DAYS_PER_MONTH: m * TRADING_DAYS_PER_MONTH]
        running += sum(chunk)
        rows.append({"month": m, "cumulative_pnl": round(running, 2)})
    return rows
