"""Unit tests for the simulation engine — pure, DLL-free, run with `pytest`."""

import engine
from engine import Params, drawdown_line
import data_loader


# ── drawdown line ────────────────────────────────────────────────────────────
def test_static_dd_line():
    assert drawdown_line(60000, 50000, 2000, "static") == 48000


def test_trailing_dd_locks_at_breakeven():
    # high-water above start, trailing line would be 58000 but locks at start
    assert drawdown_line(60000, 50000, 2000, "eod", locks_at_breakeven=True) == 50000


def test_trailing_dd_below_start_trails():
    # high-water near start, line trails below
    assert drawdown_line(50500, 50000, 2000, "eod") == 48500


# ── consistency ─────────────────────────────────────────────────────────────
def test_consistency_pass_and_fail():
    assert engine._consistency_ok([100, 100, 100], 50) is True       # best 33%
    assert engine._consistency_ok([300, 50, 50], 50) is False        # best 75%
    assert engine._consistency_ok([300, 50, 50], 0) is True          # rule off


# ── account journey ─────────────────────────────────────────────────────────
def test_winning_stream_passes_audition():
    p = Params(audition_target=300, audition_dd=2000, audition_consistency_pct=0,
               challenge_fee=100, use_vps=False)
    stream = [100.0] * 30        # +3000 quickly, never near drawdown
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 1
    assert r["attempts"] == 1
    assert r["fees"] == 100      # one challenge fee, no VPS


def test_losing_stream_fails_and_retries():
    p = Params(audition_target=3000, audition_dd=500, audition_consistency_pct=0,
               challenge_fee=100, use_vps=False)
    stream = [-200.0] * 30       # blows every attempt
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 0
    assert r["attempts"] >= 2    # retried after blowing
    assert r["fees"] == r["attempts"] * 100


def test_funded_banks_payouts():
    # easy audition then steady funded gains -> at least one payout
    p = Params(audition_target=200, audition_dd=5000, audition_consistency_pct=0,
               payout_trigger=54000, funded_start=50000, payout_amount=2000,
               funded_dd=5000, min_payout_days=0, payout_delay_days=0,
               challenge_fee=0, activation_fee=0, use_vps=False,
               funded_dd_lock_profit=100000)
    stream = [100.0] * 200
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 1
    assert r["payouts"] >= 1
    assert r["payout_cash"] >= 2000


# ── period aggregation ──────────────────────────────────────────────────────
def test_run_returns_all_horizons():
    by_day, _, _ = data_loader.sample_trades()
    series = engine.daily_series(by_day)
    res = engine.run(series, Params())
    assert set(res.keys()) == {12, 18, 24}
    for m in (12, 18, 24):
        assert res[m]["total_traders"] > 0
        assert 0 <= res[m]["pass_rate"] <= 100


def test_empty_series_is_safe():
    res = engine.simulate_period([], 12, Params())
    assert res["total_traders"] == 0
    assert res["pass_rate"] == 0.0


# ── CSV parsing ─────────────────────────────────────────────────────────────
def test_money_parsing():
    assert data_loader._money_to_float("$1,234.50") == 1234.50
    assert data_loader._money_to_float("($45.00)") == -45.0
    assert data_loader._money_to_float("-12") == -12.0
    assert data_loader._money_to_float("") == 0.0


def test_load_ninjatrader_style_csv():
    by_day, _, _ = data_loader.sample_trades(seed_days=40)
    series = engine.daily_series(by_day)
    assert len(series) > 0
    assert isinstance(series[0], float)
