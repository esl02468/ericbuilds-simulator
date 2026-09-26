"""
Prop-firm profit-farming simulation engine — pure functions, no I/O.

Replays a strategy's *real* historical trades against typical prop-firm
evaluation + funded rules and estimates the economics of "farming" payouts
across many staggered accounts over 12 / 18 / 24-month horizons.

MODEL (transparent + tweakable — these are documented assumptions, not gospel):
  * Trades are collapsed into a per-trading-day P&L series ($ per day).
  * A "trader" starts on a given trading day and runs the strategy forward
    until the horizon ends. New traders start every day / week / month — the
    staircase that models scaling the operation up over time. Each trader runs
    `num_accounts` identical accounts in parallel (copy-trading).
  * AUDITION (the eval): accumulate daily P&L from a 0 baseline. PASS when
    profit >= audition_target AND the consistency rule (if on) is satisfied.
    BLOW when the balance touches the drawdown line -> pay another challenge
    fee and start a fresh attempt, until the trader's runway runs out.
  * FUNDED: keep accumulating. Until +funded_dd_lock_profit the trailing
    drawdown still applies; once reached, the line LOCKS at breakeven for good
    (the Apex/MFFU "threshold stops trailing" mechanic) and the account only
    fails if profit drops below 0. Every time profit reaches payout_trigger
    (profit terms) with min_payout_days qualifying days and the funded
    consistency rule satisfied, withdraw payout_amount, bank it, and sit out
    payout_delay_days for approval. BLOW on a drawdown breach; optionally
    re-enter the audition afterwards (restart_after_blow).
  * COSTS: challenge fee per audition attempt (one-time) or per audition month
    (monthly billing); activation fee once per funding; VPS monthly for the
    months the trader is actually active. Per-account fees scale by
    num_accounts (each account is its own eval); VPS is one box per trader.
  * Net profit = total payouts - total costs.   ROI = net / costs.

Everything is deterministic (each trader just starts at a different offset into
the cycled trade history) so results are reproducible and unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence, Tuple

TRADING_DAYS_PER_MONTH = 21

# outcome labels for per-trader rows
OUTCOME_AUDITION_BLOWN = "Blown in audition"
OUTCOME_AUDITION_RUNNING = "Still in audition"
OUTCOME_FUNDED_ACTIVE = "Funded (active)"
OUTCOME_FUNDED_BLOWN = "Funded, then blown"


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
    audition_target: float = 3000.0         # profit that passes the eval
    audition_dd: float = 2000.0
    audition_dd_type: str = "eod"           # eod | trailing | static
    audition_dll: float = 0.0               # daily loss limit (0 = off)
    audition_consistency_pct: float = 50.0  # 0 = off
    audition_min_days: int = 0              # minimum trading days before passing (0 = off)
    challenge_fee: float = 91.0
    challenge_billing: str = "one-time"     # one-time | monthly

    # funded phase (all $ thresholds are PROFIT relative to the funded start)
    funded_start: float = 50000.0
    payout_trigger: float = 4000.0          # profit that unlocks a payout
    payout_amount: float = 2000.0
    funded_dd: float = 2000.0
    funded_dd_type: str = "eod"
    funded_dll: float = 0.0
    funded_consistency_pct: float = 0.0     # 0 = off; checked at payout time
    funded_dd_lock_profit: float = 2100.0   # after +this profit the line locks at breakeven
    activation_fee: float = 0.0
    min_payout_days: int = 5                # qualifying days ($50+) before a payout
    min_payout_day_pnl: float = 50.0
    payout_delay_days: int = 5              # approval wait after each payout (no trading)
    restart_after_blow: bool = False        # re-enter the audition after a funded blow-up

    # costs
    use_vps: bool = True
    vps_monthly: float = 199.0

    def to_dict(self) -> Dict:
        return asdict(self)


def _months(days: int) -> int:
    """Whole billing months covered by `days` of activity (minimum 1)."""
    return max(1, math.ceil(days / TRADING_DAYS_PER_MONTH))


def _consistency_ok(day_pnls: Sequence[float], pct: float) -> bool:
    """Best single day must be <= pct% of total NET profit. 0 = rule off.

    Not satisfiable while net profit is <= 0 (there is nothing to be
    consistent against)."""
    if not pct:
        return True
    total = sum(day_pnls)
    if total <= 0:
        return False
    best = max(day_pnls) if day_pnls else 0.0
    return best <= total * pct / 100.0


def _cap_loss(pnl: float, dll: float) -> float:
    """A daily-loss-limit flattens the day at -dll (the firm closes you out)."""
    if dll and pnl < -dll:
        return -dll
    return pnl


# ── per-account journey ──────────────────────────────────────────────────────
def simulate_account(stream: Sequence[float], p: Params) -> Dict:
    """Walk one account through audition attempts then the funded phase using
    `stream` (the trader's forward daily-P&L runway, already size-scaled).

    Returns a per-account result dict, including an `events` ledger of
    (day_index, cash) tuples: fees are negative, payouts positive.
    """
    n = len(stream)
    i = 0
    attempts = 0
    passes = 0
    payouts = 0
    payout_cash = 0.0
    fees = 0.0
    audition_days = 0
    funded_days = 0
    events: List[Tuple[int, float]] = []
    outcome = OUTCOME_AUDITION_RUNNING
    final_profit = 0.0

    def charge(day: int, amount: float):
        nonlocal fees
        if amount:
            fees += amount
            events.append((day, -amount))

    while i < n:
        # ---- audition: back-to-back attempts until pass or out of runway ----
        passed = False
        while i < n and not passed:
            attempts += 1
            attempt_start_day = i
            if p.challenge_billing != "monthly":
                charge(i, p.challenge_fee)
            start = p.audition_start
            hw = start
            cum = 0.0
            days: List[float] = []
            attempt_blown = False
            while i < n:
                pnl = _cap_loss(stream[i], p.audition_dll)
                i += 1
                cum += pnl
                days.append(pnl)
                bal = start + cum
                hw = max(hw, bal)
                line = drawdown_line(hw, start, p.audition_dd, p.audition_dd_type)
                if bal <= line:
                    attempt_blown = True
                    break                           # blown -> next attempt
                if (cum >= p.audition_target
                        and len(days) >= p.audition_min_days
                        and _consistency_ok(days, p.audition_consistency_pct)):
                    passed = True
                    break
            attempt_days = i - attempt_start_day
            audition_days += attempt_days
            if p.challenge_billing == "monthly":
                # billed per month spent inside this attempt
                for m in range(_months(attempt_days)):
                    charge(attempt_start_day + m * TRADING_DAYS_PER_MONTH, p.challenge_fee)
            if not passed:
                outcome = OUTCOME_AUDITION_BLOWN if attempt_blown else OUTCOME_AUDITION_RUNNING

        if not passed:
            break

        # ---- funded: accumulate, bank payouts, until blown or runway ends ----
        passes += 1
        outcome = OUTCOME_FUNDED_ACTIVE
        charge(i, p.activation_fee)
        start = p.funded_start
        hw = start
        cum = 0.0
        qualifying = 0
        cycle: List[float] = []
        locked = False
        blown = False
        funded_start_day = i
        while i < n:
            pnl = _cap_loss(stream[i], p.funded_dll)
            i += 1
            cum += pnl
            cycle.append(pnl)
            bal = start + cum
            hw = max(hw, bal)
            if pnl >= p.min_payout_day_pnl:
                qualifying += 1
            # once profit reaches the lock level the line locks at breakeven for good
            if p.funded_dd_type != "static" and cum >= p.funded_dd_lock_profit:
                locked = True
            if locked:
                line = start                        # profit < 0 fails
            else:
                line = drawdown_line(hw, start, p.funded_dd, p.funded_dd_type)
            if bal <= line:
                blown = True
                break
            if (cum >= p.payout_trigger
                    and qualifying >= p.min_payout_days
                    and _consistency_ok(cycle, p.funded_consistency_pct)):
                payouts += 1
                payout_cash += p.payout_amount
                events.append((i - 1, p.payout_amount))
                cum -= p.payout_amount
                qualifying = 0
                cycle = []
                i = min(n, i + p.payout_delay_days)  # approval wait, no trading
        funded_days += i - funded_start_day
        final_profit = cum
        if blown:
            outcome = OUTCOME_FUNDED_BLOWN
            if p.restart_after_blow and i < n:
                continue
        break

    days_active = i
    return {
        "attempts": attempts,
        "passed": int(passes > 0),
        "passes": passes,
        "funded": int(passes > 0),
        "payouts": payouts,
        "payout_cash": round(payout_cash, 2),
        "fees": round(fees, 2),
        "audition_days": audition_days,
        "funded_days": funded_days,
        "days_active": days_active,
        "months_active": _months(days_active),
        "outcome": outcome,
        "final_profit": round(final_profit, 2),
        "events": events,
    }


# ── daily series from trades ─────────────────────────────────────────────────
def daily_series(trade_pnls_by_day: Dict[str, float]) -> List[float]:
    """Ordered list of per-day P&L from a {date_string: pnl} mapping."""
    return [float(trade_pnls_by_day[d]) for d in sorted(trade_pnls_by_day)]


def _trader_starts(total_days: int, frequency: str) -> List[int]:
    step = {"daily": 1, "weekly": 5, "monthly": TRADING_DAYS_PER_MONTH}.get(frequency, 1)
    return list(range(0, max(1, total_days), step))


# ── full simulation for one horizon ──────────────────────────────────────────
def simulate_period(series: Sequence[float], months: int, p: Params) -> Dict:
    """Simulate every trader that starts within a `months`-long horizon and
    aggregate the economics. Returns averages + totals for the period, plus a
    month-by-month cash-flow projection for the whole operation and for the
    first (representative) trader."""
    if not series:
        return _empty_period(months)

    total_days = months * TRADING_DAYS_PER_MONTH
    starts = _trader_starts(total_days, p.start_frequency)
    sized = [float(x) * p.size_multiplier for x in series]
    L = len(sized)
    n_acc = max(1, int(p.num_accounts))

    trader_rows: List[Dict] = []
    monthly_ops = [0.0] * months
    monthly_first = [0.0] * months

    for s in starts:
        runway = total_days - s
        if runway <= 0:
            continue
        # forward stream = trade history cycled from this trader's offset
        stream = [sized[(s + j) % L] for j in range(runway)]
        acct = simulate_account(stream, p)

        # one trader runs n_acc identical accounts in parallel
        per_trader_payouts = acct["payouts"] * n_acc
        per_trader_cash = acct["payout_cash"] * n_acc
        prop_fees = acct["fees"] * n_acc
        vps_fees = p.vps_monthly * acct["months_active"] if p.use_vps else 0.0
        total_fees = prop_fees + vps_fees
        net = per_trader_cash - total_fees
        roi = (net / total_fees * 100.0) if total_fees > 0 else 0.0

        # cash-flow ledger in absolute horizon days -> month buckets
        for day, cash in acct["events"]:
            m = min(months - 1, (s + day) // TRADING_DAYS_PER_MONTH)
            monthly_ops[m] += cash * n_acc
            if s == 0:
                monthly_first[m] += cash * n_acc
        if p.use_vps:
            for k in range(acct["months_active"]):
                m = min(months - 1, (s + k * TRADING_DAYS_PER_MONTH) // TRADING_DAYS_PER_MONTH)
                monthly_ops[m] -= p.vps_monthly
                if s == 0:
                    monthly_first[m] -= p.vps_monthly

        trader_rows.append({
            "start_day": s,
            "attempts": acct["attempts"],
            "passed": acct["passed"],
            "outcome": acct["outcome"],
            "days_active": acct["days_active"],
            "payouts": per_trader_payouts,
            "payout_cash": round(per_trader_cash, 2),
            "prop_fees": round(prop_fees, 2),
            "vps_fees": round(vps_fees, 2),
            "fees": round(total_fees, 2),
            "net": round(net, 2),
            "roi": round(roi, 2),
        })

    result = _aggregate(months, trader_rows)
    result["projection"] = _projection(monthly_ops, monthly_first)
    return result


def _projection(monthly_ops: List[float], monthly_first: List[float]) -> List[Dict]:
    rows, run_ops, run_first = [], 0.0, 0.0
    for m, (a, b) in enumerate(zip(monthly_ops, monthly_first), start=1):
        run_ops += a
        run_first += b
        rows.append({
            "month": m,
            "net_cash_flow": round(a, 2),
            "cumulative_net": round(run_ops, 2),
            "first_trader_cumulative_net": round(run_first, 2),
        })
    return rows


def _aggregate(months: int, rows: List[Dict]) -> Dict:
    n = len(rows)
    if n == 0:
        return _empty_period(months)
    passed = sum(r["passed"] for r in rows)

    def avg(key):
        return sum(r[key] for r in rows) / n

    def total(key):
        return sum(r[key] for r in rows)

    return {
        "months": months,
        "total_traders": n,
        "avg_attempts": round(avg("attempts"), 2),
        "passed": passed,
        "pass_rate": round(100.0 * passed / n, 1),
        "avg_payouts": round(avg("payouts"), 2),
        "avg_payout_cash": round(avg("payout_cash"), 2),
        "avg_prop_fees": round(avg("prop_fees"), 2),
        "avg_vps_fees": round(avg("vps_fees"), 2),
        "avg_fees": round(avg("fees"), 2),
        "avg_net": round(avg("net"), 2),
        "avg_roi": round(avg("roi"), 1),
        "median_net": round(_median([r["net"] for r in rows]), 2),
        "pct_positive_roi": round(100.0 * sum(1 for r in rows if r["net"] > 0) / n, 1),
        "total_payouts": int(total("payouts")),
        "total_payout_cash": round(total("payout_cash"), 2),
        "total_fees": round(total("fees"), 2),
        "total_net": round(total("net"), 2),
        "traders": rows,
    }


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = len(s) // 2
    return s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


def _empty_period(months: int) -> Dict:
    return {"months": months, "total_traders": 0, "avg_attempts": 0.0, "passed": 0,
            "pass_rate": 0.0, "avg_payouts": 0.0, "avg_payout_cash": 0.0,
            "avg_prop_fees": 0.0, "avg_vps_fees": 0.0, "avg_fees": 0.0,
            "avg_net": 0.0, "avg_roi": 0.0, "median_net": 0.0, "pct_positive_roi": 0.0,
            "total_payouts": 0, "total_payout_cash": 0.0, "total_fees": 0.0,
            "total_net": 0.0, "traders": [],
            "projection": [{"month": m, "net_cash_flow": 0.0, "cumulative_net": 0.0,
                            "first_trader_cumulative_net": 0.0}
                           for m in range(1, months + 1)]}


def run(series: Sequence[float], p: Params, horizons=(12, 18, 24)) -> Dict[int, Dict]:
    """Run every horizon. Returns {months: period_result}."""
    return {m: simulate_period(series, m, p) for m in horizons}


def monthly_projection(series: Sequence[float], p: Params, months: int = 24) -> List[Dict]:
    """Month-by-month cash-flow projection under the full rule set (payouts
    minus fees), for the whole staggered operation and for the first trader.
    Kept as a thin wrapper for backwards compatibility."""
    return simulate_period(series, months, p)["projection"]


def strategy_stats(series: Sequence[float]) -> Dict:
    """Descriptive stats of the raw daily P&L series (before any prop rules)."""
    n = len(series)
    if n == 0:
        return {"days": 0, "net": 0.0, "avg_day": 0.0, "win_rate": 0.0,
                "best_day": 0.0, "worst_day": 0.0, "max_drawdown": 0.0}
    wins = sum(1 for x in series if x > 0)
    peak = run_ = 0.0
    max_dd = 0.0
    for x in series:
        run_ += x
        peak = max(peak, run_)
        max_dd = max(max_dd, peak - run_)
    return {
        "days": n,
        "net": round(sum(series), 2),
        "avg_day": round(sum(series) / n, 2),
        "win_rate": round(100.0 * wins / n, 1),
        "best_day": round(max(series), 2),
        "worst_day": round(min(series), 2),
        "max_drawdown": round(max_dd, 2),
    }


# ── edge finder ──────────────────────────────────────────────────────────────
def _with(p: Params, **overrides) -> Params:
    d = p.to_dict()
    d.update(overrides)
    return Params(**d)


def diagnose(period: Dict) -> Dict:
    """Why traders do / don't make money in a period result: outcome mix and
    what is binding (audition, blow-ups, or payout cadence)."""
    rows = period["traders"]
    n = len(rows) or 1
    mix = {}
    for r in rows:
        mix[r["outcome"]] = mix.get(r["outcome"], 0) + 1
    funded = [r for r in rows if r["passed"]]
    paid = [r for r in funded if r["payouts"] > 0]
    return {
        "outcome_mix": {k: round(100.0 * v / n, 1) for k, v in sorted(mix.items())},
        "pct_funded": round(100.0 * len(funded) / n, 1),
        "pct_funded_paid": round(100.0 * len(paid) / max(1, len(funded)), 1),
        "pct_funded_blown": round(100.0 * sum(1 for r in funded
                                              if r["outcome"] == OUTCOME_FUNDED_BLOWN)
                                  / max(1, len(funded)), 1),
    }


def breakeven_multiplier(series: Sequence[float], p: Params, months: int = 24,
                         lo: float = 0.05, hi: float = 5.0, metric: str = "median_net") -> float:
    """Smallest size multiplier at which the period `metric` turns positive
    (bisection). Returns inf if even `hi` has no edge, 0 if `lo` already has."""
    def f(m):
        return simulate_period(series, months, _with(p, size_multiplier=m))[metric]
    if f(hi) <= 0:
        return math.inf
    if f(lo) > 0:
        return 0.0
    for _ in range(14):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return round(hi, 3)


def edge_sweep(series: Sequence[float], base: Params, variants: Dict[str, Dict],
               multipliers: Sequence[float] = (0.5, 1.0, 1.5, 2.0, 3.0),
               months: int = 24) -> List[Dict]:
    """Run every rule variant x size multiplier and report whether it has edge.

    A configuration "has edge" when the median trader ends net positive AND
    more than half of the traders end with positive ROI — i.e. it is not
    carried by the lucky early starters."""
    rows = []
    for name, overrides in variants.items():
        p = _with(base, **overrides)
        for mult in multipliers:
            r = simulate_period(series, months, _with(p, size_multiplier=mult))
            first = r["traders"][0] if r["traders"] else {"net": 0.0, "payouts": 0}
            per_acc = (r["avg_payout_cash"] - r["avg_prop_fees"]) / max(1, p.num_accounts)
            rows.append({
                "variant": name,
                "multiplier": mult,
                "avg_day": round(sum(series) / max(1, len(series)) * mult, 2),
                "pass_rate": r["pass_rate"],
                "avg_payouts": r["avg_payouts"],
                "avg_roi": r["avg_roi"],
                "median_net": r["median_net"],
                "avg_net": r["avg_net"],
                "pct_positive_roi": r["pct_positive_roi"],
                "first_trader_net": first["net"],
                "first_trader_payouts": first["payouts"],
                "operation_net": r["total_net"],
                "net_per_account_before_vps": round(per_acc, 2),
                "has_edge": bool(r["median_net"] > 0 and r["pct_positive_roi"] > 50),
                **diagnose(r),
            })
    return rows


def accounts_curve(series: Sequence[float], p: Params, counts: Sequence[int] = (1, 2, 3, 5, 10),
                   months: int = 24) -> List[Dict]:
    """Average ROI / net per trader as the number of parallel accounts grows.
    Prop fees and payouts scale with accounts; the VPS does not."""
    out = []
    for n in counts:
        r = simulate_period(series, months, _with(p, num_accounts=int(n)))
        out.append({"accounts": int(n), "avg_net": r["avg_net"], "median_net": r["median_net"],
                    "avg_roi": r["avg_roi"], "pct_positive_roi": r["pct_positive_roi"],
                    "operation_net": r["total_net"]})
    return out
