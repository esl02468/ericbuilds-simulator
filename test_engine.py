"""Unit tests for the simulation engine + loader — pure, run with `pytest`."""

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
    assert drawdown_line(50500, 50000, 2000, "eod") == 48500


# ── consistency ─────────────────────────────────────────────────────────────
def test_consistency_pass_and_fail():
    assert engine._consistency_ok([100, 100, 100], 50) is True       # best 33%
    assert engine._consistency_ok([300, 50, 50], 50) is False        # best 75%
    assert engine._consistency_ok([300, 50, 50], 0) is True          # rule off
    assert engine._consistency_ok([300, -400], 50) is False          # no net profit


def test_consistency_uses_net_profit():
    # best day 400, net 600 -> 66% > 50% -> fails; with positive-only sum (700) it would be 57%
    assert engine._consistency_ok([300, -100, 400], 50) is False
    assert engine._consistency_ok([300, -100, 400], 70) is True


# ── account journey ─────────────────────────────────────────────────────────
def test_winning_stream_passes_audition():
    p = Params(audition_target=300, audition_dd=2000, audition_consistency_pct=0,
               challenge_fee=100, use_vps=False)
    stream = [100.0] * 30
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 1
    assert r["attempts"] == 1
    assert r["fees"] == 100      # one challenge fee
    assert r["outcome"] == engine.OUTCOME_FUNDED_ACTIVE


def test_losing_stream_fails_and_retries():
    p = Params(audition_target=3000, audition_dd=500, audition_consistency_pct=0,
               challenge_fee=100, use_vps=False)
    stream = [-200.0] * 30
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 0
    assert r["attempts"] == 10   # blown every 3 days
    assert r["fees"] == r["attempts"] * 100
    assert r["outcome"] == engine.OUTCOME_AUDITION_BLOWN


def test_audition_consistency_delays_pass():
    # one huge day then small days: needs more small profit before it can pass
    p = Params(audition_target=1000, audition_dd=5000, audition_consistency_pct=50,
               challenge_fee=0, use_vps=False)
    stream = [1000.0] + [100.0] * 30
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 1
    # passes when 1000 <= 50% of net -> net >= 2000 -> after 10 more days
    assert r["audition_days"] == 11


def test_audition_min_days():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               audition_min_days=7, challenge_fee=0, use_vps=False)
    r = engine.simulate_account([100.0] * 20, p)
    assert r["passed"] == 1 and r["audition_days"] == 7


def test_payout_trigger_is_profit_terms():
    """Regression: the trigger used to be balance-relative (4000 - 50000 = -46000),
    firing on day one and blowing the account on the withdrawal."""
    p = Params(audition_target=300, audition_dd=2000, audition_consistency_pct=0,
               payout_trigger=4000, payout_amount=2000, funded_dd=2000,
               funded_dd_lock_profit=2100, min_payout_days=5, payout_delay_days=5,
               challenge_fee=0, use_vps=False)
    r = engine.simulate_account([100.0] * 200, p)
    assert r["outcome"] == engine.OUTCOME_FUNDED_ACTIVE
    # audition 3 days; first payout after 40 funded days, then every 20 + 5 wait
    assert r["payouts"] == 1 + (200 - 3 - 40) // 25
    assert all(cash > 0 for _, cash in r["events"])   # fee=0 -> only payout events


def test_payout_withdrawal_does_not_blow_locked_account():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               payout_trigger=3000, payout_amount=2500, funded_dd=2000,
               funded_dd_lock_profit=2100, min_payout_days=0, payout_delay_days=0,
               challenge_fee=0, use_vps=False)
    r = engine.simulate_account([500.0] * 20, p)
    # after the withdrawal profit is +500, above breakeven -> still alive
    assert r["outcome"] == engine.OUTCOME_FUNDED_ACTIVE
    assert r["payouts"] >= 2


def test_funded_blows_before_lock():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               funded_dd=1000, funded_dd_lock_profit=2100, challenge_fee=0, use_vps=False)
    stream = [200.0] + [-600.0] * 5
    r = engine.simulate_account(stream, p)
    assert r["passed"] == 1
    assert r["outcome"] == engine.OUTCOME_FUNDED_BLOWN
    assert r["days_active"] == 3          # +200 pass, -600, -600 -> -1000 <= line


def test_locked_account_only_fails_below_zero():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               funded_dd=1000, funded_dd_lock_profit=2000, payout_trigger=99999,
               challenge_fee=0, use_vps=False)
    stream = [200.0, 2000.0, -1500.0, -300.0, -100.0, -200.0, -200.0]
    r = engine.simulate_account(stream, p)
    # funded: +2000 (locked) -> 500 -> 200 -> 100 -> -100 blown on day 6 of funded
    assert r["outcome"] == engine.OUTCOME_FUNDED_BLOWN
    assert r["funded_days"] == 5


def test_funded_consistency_gates_payout():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               payout_trigger=1000, payout_amount=500, funded_dd=5000,
               funded_consistency_pct=50, min_payout_days=0, payout_delay_days=0,
               challenge_fee=0, use_vps=False)
    stream = [100.0, 1000.0] + [100.0] * 10
    r = engine.simulate_account(stream, p)
    # 1000 of 1100 is 91% -> blocked until net >= 2000 (10 more days)
    assert r["payouts"] == 1
    assert r["events"][-1][0] == 11


def test_min_qualifying_days_gate():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               payout_trigger=500, payout_amount=500, funded_dd=5000,
               min_payout_days=3, min_payout_day_pnl=50, payout_delay_days=0,
               challenge_fee=0, use_vps=False)
    stream = [100.0, 600.0, 10.0, 10.0, 60.0, 60.0]
    r = engine.simulate_account(stream, p)
    assert r["payouts"] == 1
    assert r["events"][-1][0] == 5       # 600, 60, 60 are the three qualifying days


def test_daily_loss_limit_caps_day():
    p = Params(audition_target=100, audition_dd=1500, audition_dll=500,
               audition_consistency_pct=0, challenge_fee=0, use_vps=False)
    r = engine.simulate_account([-2000.0, -2000.0, 300.0, 300.0], p)
    assert r["attempts"] == 1            # -500 -500 +300 +300 -> never blown
    assert r["passed"] == 0


def test_restart_after_funded_blow():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               funded_dd=500, funded_dd_lock_profit=99999, challenge_fee=10,
               activation_fee=5, use_vps=False, restart_after_blow=True)
    stream = [200.0, -600.0] * 5
    r = engine.simulate_account(stream, p)
    assert r["attempts"] == 5 and r["passes"] == 5
    assert r["fees"] == 5 * 10 + 5 * 5
    assert r["outcome"] == engine.OUTCOME_FUNDED_BLOWN


def test_monthly_billing_charges_per_audition_month():
    p = Params(audition_target=99999, audition_dd=99999, audition_consistency_pct=0,
               challenge_fee=100, challenge_billing="monthly", use_vps=False)
    r = engine.simulate_account([1.0] * 45, p)     # 45 days = 3 billing months
    assert r["attempts"] == 1
    assert r["fees"] == 300
    assert [d for d, _ in r["events"]] == [0, 21, 42]


# ── period aggregation ──────────────────────────────────────────────────────
def test_run_returns_all_horizons():
    by_day, _, _ = data_loader.sample_trades()
    series = engine.daily_series(by_day)
    res = engine.run(series, Params())
    assert set(res.keys()) == {12, 18, 24}
    for m in (12, 18, 24):
        r = res[m]
        assert r["total_traders"] == m * engine.TRADING_DAYS_PER_MONTH
        assert 0 <= r["pass_rate"] <= 100
        assert len(r["projection"]) == m
        assert abs(r["projection"][-1]["cumulative_net"] - r["total_net"]) < 0.01
        assert abs(r["projection"][-1]["first_trader_cumulative_net"] - r["traders"][0]["net"]) < 0.01


def test_sample_strategy_is_profitable_under_defaults():
    by_day, _, _ = data_loader.sample_trades()
    series = engine.daily_series(by_day)
    r = engine.simulate_period(series, 24, Params())
    first = r["traders"][0]
    assert first["passed"] == 1
    assert first["payouts"] >= 10
    assert first["net"] > 0


def test_num_accounts_scales_payouts_and_prop_fees_not_vps():
    by_day, _, _ = data_loader.sample_trades()
    series = engine.daily_series(by_day)
    one = engine.simulate_period(series, 12, Params(num_accounts=1))["traders"][0]
    three = engine.simulate_period(series, 12, Params(num_accounts=3))["traders"][0]
    assert three["payout_cash"] == 3 * one["payout_cash"]
    assert three["prop_fees"] == 3 * one["prop_fees"]
    assert three["vps_fees"] == one["vps_fees"]


def test_vps_billed_for_active_months_only():
    p = Params(audition_target=100, audition_dd=5000, audition_consistency_pct=0,
               funded_dd=500, funded_dd_lock_profit=99999, challenge_fee=0,
               use_vps=True, vps_monthly=100, start_frequency="monthly")
    series = [200.0, -600.0] + [0.0] * 19        # 21-day cycle = monthly start offsets
    r = engine.simulate_period(series, 12, p)
    # every trader passes on day 1, blows the funded account on day 2, and stops
    assert all(t["outcome"] == engine.OUTCOME_FUNDED_BLOWN for t in r["traders"])
    assert all(t["vps_fees"] == 100 for t in r["traders"])


def test_start_frequencies():
    assert len(engine._trader_starts(42, "daily")) == 42
    assert len(engine._trader_starts(42, "weekly")) == 9
    assert len(engine._trader_starts(42, "monthly")) == 2


def test_empty_series_is_safe():
    res = engine.simulate_period([], 12, Params())
    assert res["total_traders"] == 0
    assert res["pass_rate"] == 0.0
    assert len(res["projection"]) == 12
    assert engine.strategy_stats([])["days"] == 0


def test_strategy_stats():
    s = engine.strategy_stats([100, -50, 200, -300, 50])
    assert s["days"] == 5 and s["net"] == 0 and s["win_rate"] == 60.0
    assert s["max_drawdown"] == 300   # peak +250 -> trough -50


# ── CSV parsing ─────────────────────────────────────────────────────────────
def test_money_parsing():
    f = data_loader._money_to_float
    assert f("$1,234.50") == 1234.50
    assert f("($45.00)") == -45.0
    assert f("-12") == -12.0
    assert f("$-45") == -45.0
    assert f("1.234,50") == 1234.5
    assert f("") == 0.0
    assert f(float("nan")) == 0.0


def test_sample_has_exact_trading_days():
    by_day, df, _ = data_loader.sample_trades(seed_days=40)
    assert len(by_day) == 40 and len(df) == 40
    assert all(pd_weekday < 5 for pd_weekday in
               (__import__("pandas").Timestamp(d).weekday() for d in by_day))


def test_load_ninjatrader_style_csv_roundtrip():
    by_day, df, _ = data_loader.sample_trades(seed_days=40)
    csv = df.to_csv(index=False).encode("utf-8")
    loaded, _, note = data_loader.load_trades(csv)
    assert loaded == by_day                      # 'Cum. net profit' must be ignored
    assert "Profit" in note and "Exit time" in note


def test_load_semicolon_bom_and_commission():
    csv = (b"\xef\xbb\xbfTrade number;Exit time;Profit;Cum. net profit;Commission\n"
           b"1;1/2/2024 10:15:00 AM;$120.00;$120.00;$1.00\n"
           b"2;1/2/2024 11:30:00 AM;($40.00);$80.00;$1.00\n"
           b"3;1/3/2024 9:55:00 AM;$50.00;$130.00;$1.00\n")
    by_day, _, _ = data_loader.load_trades(csv)
    assert by_day == {"2024-01-02": 80.0, "2024-01-03": 50.0}
    by_day, _, _ = data_loader.load_trades(csv, subtract_commission=True)
    assert by_day == {"2024-01-02": 78.0, "2024-01-03": 49.0}


def test_load_without_date_column():
    by_day, _, note = data_loader.load_trades(b"pnl\n100\n-50\n")
    assert list(by_day.values()) == [100.0, -50.0]
    assert "no date" in note


def test_load_rejects_missing_pnl():
    try:
        data_loader.load_trades(b"a,b\n1,2\n")
    except ValueError as e:
        assert "profit" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")
