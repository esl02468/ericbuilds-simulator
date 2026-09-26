"""
Prop Firm Profit Farming Calculator — Streamlit front-end.

Upload your strategy's trade history (or use the sample), set typical prop-firm
audition + funded rules, and see how the economics play out across staggered
accounts over 12 / 18 / 24 months. Deploys to Streamlit Community Cloud and
embeds into ericbuilds.xyz with a single <iframe>.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data_loader
import engine
from engine import Params

st.set_page_config(page_title="Prop Firm Profit Farming Calculator",
                   page_icon="📈", layout="wide")

# ── light styling on top of the dark theme (see .streamlit/config.toml) ──
st.markdown("""
<style>
  .block-container { padding-top: 2rem; }
  h1, h2, h3 { color: #4a9eff; }
  div[data-testid="stMetric"] {
      background: #16181d; border: 1px solid #23262d;
      border-radius: 12px; padding: 14px 16px;
  }
  div[data-testid="stMetricValue"] { color: #4a9eff; }
</style>
""", unsafe_allow_html=True)

DD_LABELS = {"eod": "End of Day Drawdown", "trailing": "Trailing (Intraday)", "static": "Static"}
DD_FROM_LABEL = {v: k for k, v in DD_LABELS.items()}
BILLING_LABELS = {"one-time": "One-Time", "monthly": "Monthly"}
BILLING_FROM_LABEL = {v: k for k, v in BILLING_LABELS.items()}

# ── firm presets (approximate public rules — always verify with the firm) ────
PRESETS: dict[str, dict] = {
    "Reference defaults (50K)": {},
    "Apex 50K (approx.)": dict(
        audition_target=3000, audition_dd=2500, audition_dd_type="trailing",
        audition_dll=0, audition_consistency_pct=0, challenge_fee=167,
        challenge_billing="monthly", activation_fee=140,
        payout_trigger=3600, payout_amount=1000, funded_dd=2500,
        funded_dd_type="trailing", funded_dll=0, funded_consistency_pct=50,
        funded_dd_lock_profit=2600, min_payout_days=8, min_payout_day_pnl=50,
        payout_delay_days=3,
    ),
    "MFFU Starter 50K (approx.)": dict(
        audition_target=3000, audition_dd=2000, audition_dd_type="eod",
        audition_dll=0, audition_consistency_pct=50, challenge_fee=91,
        challenge_billing="monthly", activation_fee=0,
        payout_trigger=4000, payout_amount=2000, funded_dd=2000,
        funded_dd_type="eod", funded_dll=0, funded_consistency_pct=40,
        funded_dd_lock_profit=2100, min_payout_days=5, min_payout_day_pnl=50,
        payout_delay_days=5,
    ),
    "TopStep 50K (approx.)": dict(
        audition_target=3000, audition_dd=2000, audition_dd_type="eod",
        audition_dll=1000, audition_consistency_pct=50, challenge_fee=49,
        challenge_billing="monthly", activation_fee=149,
        payout_trigger=3000, payout_amount=1500, funded_dd=2000,
        funded_dd_type="eod", funded_dll=1000, funded_consistency_pct=50,
        funded_dd_lock_profit=2000, min_payout_days=5, min_payout_day_pnl=200,
        payout_delay_days=2,
    ),
}

# widget key -> Params field (or UI-only state) with its default
_DEFAULTS = Params()
WIDGET_DEFAULTS: dict[str, object] = {
    "num_accounts": _DEFAULTS.num_accounts,
    "audition_target": _DEFAULTS.audition_target,
    "audition_dd": _DEFAULTS.audition_dd,
    "audition_dd_type": DD_LABELS[_DEFAULTS.audition_dd_type],
    "aud_dll_on": bool(_DEFAULTS.audition_dll),
    "audition_dll": _DEFAULTS.audition_dll or 1000.0,
    "aud_cons_on": bool(_DEFAULTS.audition_consistency_pct),
    "audition_consistency_pct": _DEFAULTS.audition_consistency_pct or 50.0,
    "audition_min_days": _DEFAULTS.audition_min_days,
    "challenge_fee": _DEFAULTS.challenge_fee,
    "challenge_billing": BILLING_LABELS[_DEFAULTS.challenge_billing],
    "payout_trigger": _DEFAULTS.payout_trigger,
    "payout_amount": _DEFAULTS.payout_amount,
    "funded_dd": _DEFAULTS.funded_dd,
    "funded_dd_type": DD_LABELS[_DEFAULTS.funded_dd_type],
    "fund_dll_on": bool(_DEFAULTS.funded_dll),
    "funded_dll": _DEFAULTS.funded_dll or 1000.0,
    "fund_cons_on": bool(_DEFAULTS.funded_consistency_pct),
    "funded_consistency_pct": _DEFAULTS.funded_consistency_pct or 50.0,
    "funded_dd_lock_profit": _DEFAULTS.funded_dd_lock_profit,
    "activation_fee": _DEFAULTS.activation_fee,
    "min_payout_days": _DEFAULTS.min_payout_days,
    "min_payout_day_pnl": _DEFAULTS.min_payout_day_pnl,
    "payout_delay_days": _DEFAULTS.payout_delay_days,
    "restart_after_blow": _DEFAULTS.restart_after_blow,
    "use_vps": _DEFAULTS.use_vps,
    "vps_monthly": _DEFAULTS.vps_monthly,
    "start_frequency": _DEFAULTS.start_frequency.title(),
    "size_multiplier": _DEFAULTS.size_multiplier,
}


def _init_state():
    for k, v in WIDGET_DEFAULTS.items():
        st.session_state.setdefault(k, v)


def _apply_preset():
    """Write a preset's values into the widget state (runs before widgets render)."""
    values = dict(WIDGET_DEFAULTS)
    for k, v in PRESETS.get(st.session_state.get("preset", ""), {}).items():
        if k in ("audition_dd_type", "funded_dd_type"):
            values[k] = DD_LABELS[v]
        elif k == "challenge_billing":
            values[k] = BILLING_LABELS[v]
        elif k == "audition_dll":
            values["aud_dll_on"] = bool(v)
            if v:
                values[k] = float(v)
        elif k == "funded_dll":
            values["fund_dll_on"] = bool(v)
            if v:
                values[k] = float(v)
        elif k == "audition_consistency_pct":
            values["aud_cons_on"] = bool(v)
            if v:
                values[k] = float(v)
        elif k == "funded_consistency_pct":
            values["fund_cons_on"] = bool(v)
            if v:
                values[k] = float(v)
        elif isinstance(WIDGET_DEFAULTS[k], float):
            values[k] = float(v)
        elif isinstance(WIDGET_DEFAULTS[k], int) and not isinstance(WIDGET_DEFAULTS[k], bool):
            values[k] = int(v)
        else:
            values[k] = v
    for k, v in values.items():
        st.session_state[k] = v


# ── sidebar: all the knobs ───────────────────────────────────────────────────
def sidebar() -> tuple[Params, object, bool, bool]:
    _init_state()
    sb = st.sidebar
    sb.markdown("## Simulation Settings")

    sb.markdown("#### Upload Trade Data")
    upload = sb.file_uploader("Choose a CSV file with trade data", type=["csv", "txt"],
                              help="NinjaTrader 'Trades' grid export, or any CSV with a "
                                   "Profit/PnL column and (optionally) a date column.")
    use_sample = upload is None or sb.checkbox("Use sample data instead of my file", value=False)
    subtract_commission = sb.checkbox(
        "Subtract Commission column (if present)", value=False,
        help="Only matters if your export's Profit column is gross of commissions.")

    sb.markdown("#### Firm Preset")
    sb.selectbox("Load rules from a preset", list(PRESETS), key="preset", on_change=_apply_preset,
                 help="Approximate public rules for common firms. Always verify against the "
                      "firm's current rule page — every field stays editable below.")

    sb.markdown("#### Number of Accounts")
    sb.number_input("Number of Accounts", min_value=1, max_value=100, step=1, key="num_accounts",
                    help="Identical accounts a single trader runs in parallel (copy-trading).")

    sb.markdown("### Audition Phase Settings")
    sb.number_input("Audition Profit Target ($)", min_value=0.0, step=100.0, key="audition_target")
    sb.selectbox("Audition Drawdown Type:", list(DD_LABELS.values()), key="audition_dd_type",
                 help="End-of-day and intraday trailing are both evaluated on daily P&L "
                      "(the data has no intraday path); both lock at breakeven.")
    sb.number_input("Audition Max Drawdown ($)", min_value=0.0, step=100.0, key="audition_dd")
    sb.checkbox("Enable Daily Loss Limit (Audition)", key="aud_dll_on")
    if st.session_state.aud_dll_on:
        sb.number_input("Audition Daily Loss Limit ($)", min_value=0.0, step=50.0, key="audition_dll")
    sb.checkbox("Enable Consistency Rule (Audition)", key="aud_cons_on")
    if st.session_state.aud_cons_on:
        sb.number_input("Audition Consistency %", min_value=0.0, max_value=100.0, step=5.0,
                        key="audition_consistency_pct",
                        help="Best single day may be at most this % of total net profit.")
    sb.number_input("Minimum Trading Days to Pass", min_value=0, max_value=60, step=1,
                    key="audition_min_days", help="0 = no minimum.")
    sb.number_input("Challenge Fee ($)", min_value=0.0, step=1.0, key="challenge_fee")
    sb.selectbox("Challenge Fee Billing:", list(BILLING_LABELS.values()), key="challenge_billing",
                 help="One-Time: charged per attempt. Monthly: charged per month spent in the audition.")

    sb.markdown("### Funded Phase Settings")
    sb.number_input("Payout Trigger — Profit ($)", min_value=0.0, step=100.0, key="payout_trigger",
                    help="Profit above the funded starting balance that unlocks a payout.")
    sb.number_input("Payout Amount ($)", min_value=0.0, step=100.0, key="payout_amount")
    sb.selectbox("Funded Drawdown Type:", list(DD_LABELS.values()), key="funded_dd_type")
    sb.number_input("Funded Max Drawdown ($)", min_value=0.0, step=100.0, key="funded_dd")
    sb.number_input("Drawdown Locks at Breakeven After Profit ($)", min_value=0.0, step=100.0,
                    key="funded_dd_lock_profit",
                    help="Once profit reaches this level, the trailing drawdown stops moving and "
                         "the account only fails if profit drops below $0. Ignored for Static.")
    sb.checkbox("Enable Daily Loss Limit (Funded)", key="fund_dll_on")
    if st.session_state.fund_dll_on:
        sb.number_input("Funded Daily Loss Limit ($)", min_value=0.0, step=50.0, key="funded_dll")
    sb.checkbox("Enable Consistency Rule (Funded)", key="fund_cons_on")
    if st.session_state.fund_cons_on:
        sb.number_input("Funded Consistency %", min_value=0.0, max_value=100.0, step=5.0,
                        key="funded_consistency_pct",
                        help="Checked at payout time over the days since the last payout.")
    sb.number_input("Activation Fee ($)", min_value=0.0, step=10.0, key="activation_fee")
    with sb.expander("Payout rules"):
        st.number_input("Minimum Qualifying Days per Payout", min_value=0, max_value=60, step=1,
                        key="min_payout_days")
        st.number_input("Qualifying Day = Profit of at Least ($)", min_value=0.0, step=10.0,
                        key="min_payout_day_pnl")
        st.number_input("Payout Approval Wait (trading days)", min_value=0, max_value=30, step=1,
                        key="payout_delay_days", help="No trading while a payout is being approved.")
        st.checkbox("Re-enter the audition after a funded account is blown", key="restart_after_blow")

    sb.markdown("### VPS (Virtual Private Server)")
    sb.checkbox("Using a VPS?", key="use_vps")
    if st.session_state.use_vps:
        sb.number_input("Monthly VPS Cost ($)", min_value=0.0, step=10.0, key="vps_monthly",
                        help="One VPS per trader, billed for each month the trader is active.")

    sb.markdown("### Trader Start Frequency")
    sb.radio("New Trader Starts Every:", ["Monthly", "Weekly", "Daily"], key="start_frequency")

    sb.markdown("### Trade Sizing")
    sb.slider("Trade Size Multiplier", 0.25, 3.0, step=0.05, key="size_multiplier")

    s = st.session_state
    p = Params(
        num_accounts=int(s.num_accounts),
        size_multiplier=float(s.size_multiplier),
        start_frequency=str(s.start_frequency).lower(),
        audition_target=float(s.audition_target),
        audition_dd=float(s.audition_dd),
        audition_dd_type=DD_FROM_LABEL[s.audition_dd_type],
        audition_dll=float(s.audition_dll) if s.aud_dll_on else 0.0,
        audition_consistency_pct=float(s.audition_consistency_pct) if s.aud_cons_on else 0.0,
        audition_min_days=int(s.audition_min_days),
        challenge_fee=float(s.challenge_fee),
        challenge_billing=BILLING_FROM_LABEL[s.challenge_billing],
        payout_trigger=float(s.payout_trigger),
        payout_amount=float(s.payout_amount),
        funded_dd=float(s.funded_dd),
        funded_dd_type=DD_FROM_LABEL[s.funded_dd_type],
        funded_dll=float(s.funded_dll) if s.fund_dll_on else 0.0,
        funded_consistency_pct=float(s.funded_consistency_pct) if s.fund_cons_on else 0.0,
        funded_dd_lock_profit=float(s.funded_dd_lock_profit),
        activation_fee=float(s.activation_fee),
        min_payout_days=int(s.min_payout_days),
        min_payout_day_pnl=float(s.min_payout_day_pnl),
        payout_delay_days=int(s.payout_delay_days),
        restart_after_blow=bool(s.restart_after_blow),
        use_vps=bool(s.use_vps),
        vps_monthly=float(s.vps_monthly) if s.use_vps else 0.0,
    )
    return p, upload, use_sample, subtract_commission


def rules_md(p: Params) -> str:
    aud_cons = (f"largest single-day profit must be ≤ {p.audition_consistency_pct:.0f}% of total "
                f"net profit to pass (it doesn't blow the account — you just need more profit)"
                if p.audition_consistency_pct else "off")
    fund_cons = (f"checked at each payout — best day since the last payout ≤ "
                 f"{p.funded_consistency_pct:.0f}% of profit since then"
                 if p.funded_consistency_pct else "off")
    lock = (f"- Until +\\${p.funded_dd_lock_profit:,.0f} profit the "
            f"{DD_LABELS[p.funded_dd_type].lower()} of \\${p.funded_dd:,.0f} applies; "
            f"after that the account can only fail if profit drops below \\$0.\n"
            if p.funded_dd_type != "static" else
            f"- A static drawdown of \\${p.funded_dd:,.0f} below the starting balance applies.\n")
    return f"""
**Prop Firm Trading Rules being simulated:**

**Audition Phase:**
- **Goal:** reach +\\${p.audition_target:,.0f} before the \\${p.audition_dd:,.0f}
  {DD_LABELS[p.audition_dd_type].lower()} line is touched. Blown → pay another
  \\${p.challenge_fee:,.0f} ({BILLING_LABELS[p.challenge_billing].lower()}) and retry.
- **Consistency rule:** {aud_cons}.
- No payouts during the audition (it's a simulation account).

**Funded Phase:**
{lock}- A payout of \\${p.payout_amount:,.0f} triggers once profit reaches
  +\\${p.payout_trigger:,.0f} with at least {p.min_payout_days} qualifying days
  (≥ \\${p.min_payout_day_pnl:,.0f}); the withdrawal leaves the rest to keep trading,
  then {p.payout_delay_days} trading days pass for approval.
- **Consistency rule:** {fund_cons}.
- {"After a funded blow-up the trader re-enters the audition." if p.restart_after_blow
    else "A funded blow-up ends that trader's run (new traders keep starting on schedule)."}
"""


def metric_grid(period: dict):
    c = st.columns(3)
    c[0].metric("Audition Pass Rate", f"{period['pass_rate']}%")
    c[1].metric("Avg Payouts per Trader", f"{period['avg_payouts']}")
    c[2].metric("Avg Total Payouts", f"${period['avg_payout_cash']:,.0f}")
    c = st.columns(3)
    c[0].metric("Avg Prop Firm Fees", f"${period['avg_prop_fees']:,.0f}")
    c[1].metric("Avg VPS Cost", f"${period['avg_vps_fees']:,.0f}")
    c[2].metric("Avg Net Profit per Trader", f"${period['avg_net']:,.0f}",
                help=f"Median ${period['median_net']:,.0f}")
    c = st.columns(3)
    c[0].metric("Avg ROI", f"{period['avg_roi']}%")
    c[1].metric("% Traders with Positive ROI", f"{period['pct_positive_roi']}%")
    c[2].metric("Total Traders", f"{period['total_traders']}",
                help=f"Whole operation: {period['total_payouts']} payouts, "
                     f"${period['total_payout_cash']:,.0f} banked, "
                     f"${period['total_fees']:,.0f} spent, net ${period['total_net']:,.0f}.")


@st.cache_data(show_spinner=False)
def _run_cached(series: tuple, params: dict) -> dict:
    return engine.run(list(series), Params(**params))


EDGE_MULTIPLIERS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


@st.cache_data(show_spinner=False)
def _edge_cached(series: tuple, params: dict) -> dict:
    """Edge Finder: sweep the current rules + every preset across trade sizes."""
    base = Params(**params)
    variants = {"Your current rules": {}}
    variants.update({k: v for k, v in PRESETS.items() if v})
    s = list(series)
    return {
        "sweep": engine.edge_sweep(s, base, variants, EDGE_MULTIPLIERS, months=24),
        "breakeven": {k: engine.breakeven_multiplier(s, engine._with(base, **v))
                      for k, v in variants.items()},
        "accounts": engine.accounts_curve(s, base, counts=(1, 2, 3, 5, 10), months=24),
    }


def edge_finder_tab(series: list, p: Params, stats: dict):
    st.subheader("Edge Finder — which rules and sizing actually pay?")
    st.caption("A configuration **has edge** when the median trader ends net positive *and* more "
               "than half of all traders end with positive ROI — so it is not carried by a few "
               "lucky early starters. 24-month horizon, same start frequency and VPS as the sidebar.")
    key = (tuple(series), p.to_dict())
    if st.button("Run Edge Finder", type="primary",
                 help="Runs ~90 simulations (a few seconds). Results are cached per setting."):
        st.session_state["edge_key"] = key
    if st.session_state.get("edge_key") is None:
        st.info("Click **Run Edge Finder** to sweep your current rules and every firm preset "
                "across trade sizes and account counts.")
        return
    if st.session_state["edge_key"] != key:
        st.warning("Sidebar settings changed since the last run — click **Run Edge Finder** to refresh.")
    series_t, params_d = st.session_state["edge_key"]
    with st.spinner("Sweeping rule sets and trade sizes…"):
        res = _edge_cached(series_t, params_d)
    sweep = pd.DataFrame(res["sweep"])
    avg_day = stats["avg_day"] if stats["days"] else 0.0

    # ── verdict for the current rules at the current size ──
    cur = sweep[(sweep["variant"] == "Your current rules") & (sweep["multiplier"] == 1.0)].iloc[0]
    be = res["breakeven"]["Your current rules"]
    c = st.columns(4)
    c[0].metric("Your rules at current size", "HAS EDGE ✅" if cur["has_edge"] else "NO EDGE ❌")
    c[1].metric("Median trader net (24 mo)", f"${cur['median_net']:,.0f}")
    c[2].metric("Traders with positive ROI", f"{cur['pct_positive_roi']}%")
    if be == float("inf"):
        c[3].metric("Break-even size", "none ≤ 5×", help="Even at 5× size the median trader loses.")
    else:
        c[3].metric("Break-even size", f"{be:.2f}×",
                    help=f"≈ ${avg_day * be:,.0f}/day average needed under these rules "
                         f"(your data averages ${avg_day:,.0f}/day at 1×).")

    # ── edge map ──
    st.markdown("### Edge map — median trader net by rule set and trade size")
    pivot = sweep.pivot(index="multiplier", columns="variant", values="median_net")
    pivot.index = [f"{m}×" for m in pivot.index]
    st.line_chart(pivot)
    be_rows = [{"Rule set": k, "Break-even size": ("none ≤ 5×" if v == float("inf") else f"{v:.2f}×"),
                "Avg $/day needed": ("—" if v == float("inf") else f"${avg_day * v:,.0f}"),
                "Has edge at 1×": "✅" if bool(sweep[(sweep.variant == k) & (sweep.multiplier == 1.0)]
                                            ["has_edge"].iloc[0]) else "❌"}
               for k, v in res["breakeven"].items()]
    st.dataframe(pd.DataFrame(be_rows), width="stretch", hide_index=True)

    # ── what is binding ──
    st.markdown("### Payout cadence — average payouts per trader by trade size")
    st.caption("Where a line flattens, size no longer helps: payout amount, qualifying-day rules "
               "or the approval wait have become the binding constraint, not profit.")
    cad = sweep.pivot(index="multiplier", columns="variant", values="avg_payouts")
    cad.index = [f"{m}×" for m in cad.index]
    st.line_chart(cad)

    # ── accounts leverage ──
    st.markdown("### Copy-trading leverage — your rules, more parallel accounts")
    st.caption("Payouts and prop fees scale with accounts; the VPS does not. If one account is net "
               "positive before VPS, every extra account adds that much again.")
    acc = pd.DataFrame(res["accounts"])
    st.line_chart(acc.set_index("accounts")[["median_net", "avg_net"]])

    # ── full table ──
    st.markdown("### All configurations")
    show = sweep[["variant", "multiplier", "avg_day", "has_edge", "pass_rate", "avg_payouts",
                  "median_net", "avg_net", "avg_roi", "pct_positive_roi", "pct_funded_paid",
                  "pct_funded_blown", "operation_net"]].rename(columns={
        "variant": "Rule set", "multiplier": "Size", "avg_day": "Avg $/day", "has_edge": "Edge",
        "pass_rate": "Pass %", "avg_payouts": "Payouts/trader", "median_net": "Median net ($)",
        "avg_net": "Avg net ($)", "avg_roi": "Avg ROI %", "pct_positive_roi": "Positive ROI %",
        "pct_funded_paid": "Funded that got paid %", "pct_funded_blown": "Funded blown %",
        "operation_net": "Operation net ($)"})
    show["Edge"] = show["Edge"].map({True: "✅", False: "❌"})
    st.dataframe(show, width="stretch", hide_index=True)
    st.download_button("Download Edge Finder results as CSV",
                       show.to_csv(index=False).encode("utf-8"), "edge_finder.csv", "text/csv")


def main():
    p, upload, use_sample, subtract_commission = sidebar()

    st.title("Prop Firm Profit Farming Calculator")
    st.write("This tool replays your strategy's real trade history under typical "
             "prop-firm rules. Upload your trade history and adjust parameters in the "
             "sidebar — results update live — to see potential outcomes across many "
             "staggered traders over 12, 18 and 24 months.")
    st.info(rules_md(p))

    # load data
    try:
        if not use_sample:
            by_day, preview, note = data_loader.load_trades(upload, subtract_commission)
            st.success(f"Loaded {len(preview)} trades from **{upload.name}**. {note}")
        else:
            by_day, preview, note = data_loader.sample_trades()
            st.success("Loaded the built-in sample (ORB-style strategy, 180 trading days). "
                       "Upload your own CSV in the sidebar to simulate real trades.")
    except Exception as e:  # noqa: BLE001 — surface any parse problem to the user
        st.error(f"Could not read that CSV: {e}")
        return

    series = engine.daily_series(by_day)
    stats = engine.strategy_stats(series)
    results = _run_cached(tuple(series), p.to_dict())

    tabs = st.tabs(["Data Preview", "Simulation Results", "Trader Details",
                    "Monthly Profit Projection", "Edge Finder"])

    with tabs[0]:
        st.subheader("Trade Data Preview")
        c = st.columns(6)
        c[0].metric("Trades", f"{len(preview)}")
        c[1].metric("Trading Days", f"{stats['days']}")
        c[2].metric("Net P&L", f"${stats['net']:,.0f}")
        c[3].metric("Avg Day", f"${stats['avg_day']:,.0f}")
        c[4].metric("Winning Days", f"{stats['win_rate']}%")
        c[5].metric("Max Drawdown", f"${stats['max_drawdown']:,.0f}",
                    help=f"Best day ${stats['best_day']:,.0f} · worst day ${stats['worst_day']:,.0f}")
        st.dataframe(preview.head(500), width="stretch")
        if use_sample:
            st.download_button("Download the sample CSV (NinjaTrader-style)",
                               preview.to_csv(index=False).encode("utf-8"),
                               "sample_trades.csv", "text/csv",
                               help="Use it as a template for the columns the loader expects.")
        daily = pd.DataFrame({"day": sorted(by_day), "pnl": [by_day[d] for d in sorted(by_day)]})
        daily["cumulative"] = daily["pnl"].cumsum()
        st.caption("Raw cumulative P&L of the strategy (before any prop-firm rules), "
                   "scaled by the size multiplier.")
        st.line_chart(daily.set_index("day")["cumulative"] * p.size_multiplier)

    with tabs[1]:
        st.subheader("Simulation Parameters")
        st.markdown(f"- **Trading Periods:** 12, 18 and 24 months (all simulated)\n"
                    f"- **New Trader Start Frequency:** {p.start_frequency.title()}\n"
                    f"- **Accounts per Trader:** {p.num_accounts} · "
                    f"**Size Multiplier:** {p.size_multiplier}×\n"
                    f"- The trade history is cycled when a trader's runway is longer "
                    f"than the dataset ({stats['days']} days).")
        for m in (12, 18, 24):
            st.markdown(f"### {m} Month Period")
            metric_grid(results[m])

        st.markdown("### Detailed Statistics — All Periods")
        stat_rows = [{
            "Period (Months)": r["months"],
            "Total Traders": r["total_traders"],
            "Avg Audition Attempts": r["avg_attempts"],
            "Auditions Passed": r["passed"],
            "Pass Rate (%)": r["pass_rate"],
            "Avg Payouts/Trader": r["avg_payouts"],
            "Avg Total Payouts ($)": r["avg_payout_cash"],
            "Avg Prop Fees ($)": r["avg_prop_fees"],
            "Avg VPS ($)": r["avg_vps_fees"],
            "Avg Net Profit ($)": r["avg_net"],
            "Median Net Profit ($)": r["median_net"],
            "Avg ROI (%)": r["avg_roi"],
            "Positive ROI (%)": r["pct_positive_roi"],
            "Operation Net ($)": r["total_net"],
        } for r in (results[12], results[18], results[24])]
        stat_df = pd.DataFrame(stat_rows)
        st.dataframe(stat_df, width="stretch", hide_index=True)
        st.download_button("Download Detailed Statistics as CSV",
                           stat_df.to_csv(index=False).encode("utf-8"),
                           "prop_firm_simulation.csv", "text/csv")

    with tabs[2]:
        st.subheader("Trader Details — 24 Month Period")
        rows = results[24]["traders"]
        if rows:
            tdf = pd.DataFrame([{
                "Trader #": i + 1,
                "Start Day": r["start_day"] + 1,
                "Days Active": r["days_active"],
                "Audition Attempts": r["attempts"],
                "Passed": "✅" if r["passed"] else "❌",
                "Outcome": r["outcome"],
                "Payouts": r["payouts"],
                "Total Payouts ($)": round(r["payout_cash"], 0),
                "Prop Fees ($)": round(r["prop_fees"], 0),
                "VPS ($)": round(r["vps_fees"], 0),
                "Net Profit ($)": round(r["net"], 0),
                "ROI (%)": round(r["roi"], 1),
            } for i, r in enumerate(rows)])
            outcomes = tdf["Outcome"].value_counts()
            st.caption(" · ".join(f"{k}: {v}" for k, v in outcomes.items()))
            st.dataframe(tdf, width="stretch", hide_index=True)
            st.download_button("Download Trader Details as CSV",
                               tdf.to_csv(index=False).encode("utf-8"),
                               "trader_details.csv", "text/csv")
        else:
            st.info("No traders simulated for this period.")

    with tabs[3]:
        st.subheader("Monthly Profit Projection — 24 Month Period")
        st.caption("Cumulative net cash flow (payouts minus all fees) under the full "
                   "rule set: the whole staggered operation, and the first trader alone.")
        proj = pd.DataFrame(results[24]["projection"])
        chart = proj.rename(columns={"cumulative_net": "Whole operation",
                                     "first_trader_cumulative_net": "First trader"}
                            ).set_index("month")[["Whole operation", "First trader"]]
        st.line_chart(chart)
        show = proj.rename(columns={"month": "Month", "net_cash_flow": "Net Cash Flow ($)",
                                    "cumulative_net": "Cumulative Net ($)",
                                    "first_trader_cumulative_net": "First Trader Cumulative ($)"})
        st.dataframe(show, width="stretch", hide_index=True)

    with tabs[4]:
        edge_finder_tab(series, p, stats)


if __name__ == "__main__":
    main()
