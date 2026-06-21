"""
Prop Firm Profit Farming Calculator — Streamlit front-end.

Upload your strategy's trade history (or use the sample), set typical prop-firm
audition + funded rules, and see how the economics play out across staggered
accounts over 12 / 18 / 24 months. Deploys to Streamlit Community Cloud and
embeds into ericbuilds.xyz with a single <iframe>.
"""

import pandas as pd
import streamlit as st

import engine
from engine import Params
import data_loader

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


# ── sidebar: all the knobs ───────────────────────────────────────────────────
def sidebar() -> tuple[Params, object]:
    sb = st.sidebar
    sb.markdown("## Simulation Settings")

    sb.markdown("#### Upload Trade Data")
    upload = sb.file_uploader("Choose a CSV file with trade data", type=["csv"])
    use_sample = sb.checkbox("Use sample data instead",
                             value=upload is None,
                             help="A built-in ORB-style dataset so you can try the tool instantly.")

    sb.markdown("#### Number of Accounts")
    num_accounts = sb.number_input("Number of Accounts", min_value=1, max_value=100, value=1,
                                   help="Identical accounts a single trader runs in parallel.")

    sb.markdown("### Audition Phase Settings")
    audition_target = sb.number_input("Audition Profit Target ($)", value=3000, step=100)
    audition_dd_type = sb.selectbox("Audition Drawdown Type:",
                                    ["End of Day Drawdown", "Trailing (Intraday)", "Static"], index=0)
    audition_dd = sb.number_input("Audition Max Drawdown ($)", value=2000, step=100)
    aud_dll_on = sb.checkbox("Enable Daily Loss Limit (Audition)", value=False)
    aud_dll = sb.number_input("Audition Daily Loss Limit ($)", value=1000, step=50) if aud_dll_on else 0
    aud_cons_on = sb.checkbox("Enable Consistency Rule (Audition)", value=True)
    aud_cons = sb.number_input("Audition Consistency %", value=50, min_value=0, max_value=100) if aud_cons_on else 0
    challenge_fee = sb.number_input("Challenge Fee ($)", value=91, step=1)
    challenge_billing = sb.selectbox("Challenge Fee Billing:", ["One-Time", "Monthly"], index=0)

    sb.markdown("### Funded Phase Settings")
    payout_trigger = sb.number_input("Full Profit Target ($)", value=4000, step=100,
                                     help="Balance (profit terms) that unlocks a payout.")
    payout_amount = sb.number_input("Payout Amount ($)", value=2000, step=100)
    funded_dd_type = sb.selectbox("Funded Drawdown Type:",
                                  ["End of Day Drawdown", "Trailing (Intraday)", "Static"], index=0)
    funded_dd = sb.number_input("Funded Max Drawdown ($)", value=2000, step=100)
    fund_dll_on = sb.checkbox("Enable Daily Loss Limit (Funded)", value=False)
    fund_dll = sb.number_input("Funded Daily Loss Limit ($)", value=1000, step=50) if fund_dll_on else 0
    fund_cons_on = sb.checkbox("Enable Consistency Rule (Funded)", value=False)
    fund_cons = sb.number_input("Funded Consistency %", value=50, min_value=0, max_value=100) if fund_cons_on else 0
    activation_fee = sb.number_input("Activation Fee ($)", value=0, step=10)

    sb.markdown("### VPS (Virtual Private Server)")
    use_vps = sb.checkbox("Using a VPS?", value=True)
    vps_monthly = sb.number_input("Monthly VPS Cost ($)", value=199, step=10) if use_vps else 0

    sb.markdown("### Trader Start Frequency")
    freq_label = sb.radio("New Trader Starts Every:", ["Monthly", "Weekly", "Daily"], index=2)

    sb.markdown("### Trade Sizing")
    size_mult = sb.slider("Trade Size Multiplier", 0.5, 2.0, 1.0, 0.05)

    run = sb.button("Run Simulation", type="primary", use_container_width=True)

    dd_map = {"End of Day Drawdown": "eod", "Trailing (Intraday)": "trailing", "Static": "static"}
    p = Params(
        num_accounts=int(num_accounts),
        size_multiplier=float(size_mult),
        start_frequency=freq_label.lower(),
        audition_target=float(audition_target),
        audition_dd=float(audition_dd),
        audition_dd_type=dd_map[audition_dd_type],
        audition_dll=float(aud_dll),
        audition_consistency_pct=float(aud_cons),
        challenge_fee=float(challenge_fee),
        challenge_billing=challenge_billing.lower(),
        payout_trigger=float(payout_trigger),
        payout_amount=float(payout_amount),
        funded_dd=float(funded_dd),
        funded_dd_type=dd_map[funded_dd_type],
        funded_dll=float(fund_dll),
        funded_consistency_pct=float(fund_cons),
        activation_fee=float(activation_fee),
        use_vps=bool(use_vps),
        vps_monthly=float(vps_monthly),
    )
    return p, (upload, use_sample, run)


RULES_MD = """
**Prop Firm Trading Rules:**

**Audition Phase:**
- **Goal:** Reach the profit target before hitting the max drawdown.
- **Consistency Rule:** Largest single-day profit must stay below the configured
  % of total profit to pass — it doesn't blow the account, it just means you need
  more total profit.
- No payouts during the audition (it's a simulation account).

**Funded Phase:**
- Until +\$2,100 profit: the trailing drawdown applies.
- After +\$2,100 profit: the account can only fail if the balance drops below \$0.
- A payout triggers when the balance reaches the payout target; each payout
  withdraws the payout amount, leaving the rest to keep trading.
- Calculations account for the consistency rule, ~5 minimum qualifying days, and
  a ~5-day wait for payout approval before trading resumes.
"""


def metric_grid(period: dict):
    c = st.columns(3)
    c[0].metric("Audition Pass Rate", f"{period['pass_rate']}%")
    c[1].metric("Avg Payouts per Trader", f"{period['avg_payouts']}")
    c[2].metric("Avg Prop Firm Fees", f"${period['avg_fees']:,.0f}")
    c = st.columns(3)
    c[0].metric("Avg Total Payouts", f"${period['avg_payout_cash']:,.0f}")
    c[1].metric("Avg Expenses (incl. VPS)", f"${period['avg_fees']:,.0f}")
    c[2].metric("Avg Net Profit per Trader", f"${period['avg_net']:,.0f}")
    c = st.columns(3)
    c[0].metric("Avg ROI", f"{period['avg_roi']}%")
    c[1].metric("% Positive ROI", f"{period['pct_positive_roi']}%")
    c[2].metric("Total Traders", f"{period['total_traders']}")


def main():
    p, (upload, use_sample, run) = sidebar()

    st.title("Prop Firm Profit Farming Calculator")
    st.write("This tool calculates how your trading strategy would perform under "
             "typical prop-firm rules. Upload your trade history and adjust "
             "parameters to see potential outcomes across multiple traders.")
    st.info(RULES_MD)

    # load data
    by_day, preview, note = None, None, None
    try:
        if upload is not None and not use_sample:
            by_day, preview, note = data_loader.load_trades(upload)
            st.success(f"Loaded {len(preview)} trades from your file. {note}")
        elif use_sample:
            by_day, preview, note = data_loader.sample_trades()
            st.success("Successfully loaded sample data (ORB-style strategy). "
                       "Uncheck 'Use sample data instead' and upload your own CSV to use real trades.")
        else:
            st.warning("Please upload a trade data file or use the sample data to begin.")
            return
    except Exception as e:
        st.error(f"Could not read that CSV: {e}")
        return

    series = engine.daily_series(by_day)
    tabs = st.tabs(["Data Preview", "Simulation Results", "Trader Details", "Monthly Profit Projection"])

    with tabs[0]:
        st.subheader("Trade Data Preview")
        st.dataframe(preview.head(200), use_container_width=True)
        st.caption(f"{len(preview)} trades · {len(series)} trading days · "
                   f"net ${sum(series):,.0f} over the dataset.")

    # only run the heavier simulation once asked (or on first sample load)
    if run or use_sample:
        results = engine.run(series, p)

        with tabs[1]:
            st.subheader("Simulation Parameters")
            st.markdown(f"- **Trading Periods:** 12, 18 and 24 months (all simulated)\n"
                        f"- **New Trader Start Frequency:** {p.start_frequency.title()}\n"
                        f"- **Accounts per Trader:** {p.num_accounts} · "
                        f"**Size Multiplier:** {p.size_multiplier}×")
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
                "Avg Fees ($)": r["avg_fees"],
                "Avg Net Profit ($)": r["avg_net"],
                "Avg ROI (%)": r["avg_roi"],
            } for r in (results[12], results[18], results[24])]
            stat_df = pd.DataFrame(stat_rows)
            st.dataframe(stat_df, use_container_width=True, hide_index=True)
            st.download_button("Download Detailed Statistics as CSV",
                               stat_df.to_csv(index=False).encode("utf-8"),
                               "prop_firm_simulation.csv", "text/csv")

        with tabs[2]:
            st.subheader("Trader Details — 24 Month Period")
            rows = results[24]["traders"]
            if rows:
                tdf = pd.DataFrame([{
                    "Trader #": i + 1,
                    "Audition Attempts": r["attempts"],
                    "Passed": "✅" if r["passed"] else "❌",
                    "Payouts": r["payouts"],
                    "Total Payouts ($)": round(r["payout_cash"], 0),
                    "Fees ($)": round(r["fees"], 0),
                    "Net Profit ($)": round(r["net"], 0),
                    "ROI (%)": round(r["roi"], 1),
                } for i, r in enumerate(rows)])
                st.dataframe(tdf, use_container_width=True, hide_index=True)
                st.download_button("Download Trader Details as CSV",
                                   tdf.to_csv(index=False).encode("utf-8"),
                                   "trader_details.csv", "text/csv")
            else:
                st.info("No traders simulated for this period.")

        with tabs[3]:
            st.subheader("Monthly Profit Projection")
            st.caption("Cumulative net P&L for a single representative account "
                       "(one trader starting at day 0), month by month.")
            proj = engine.monthly_projection(series, p, months=24)
            pdf = pd.DataFrame(proj).set_index("month")
            st.line_chart(pdf, y="cumulative_pnl")
            st.dataframe(pd.DataFrame(proj), use_container_width=True, hide_index=True)
    else:
        for t in tabs[1:]:
            with t:
                st.info("Set your parameters in the sidebar and click **Run Simulation**.")


if __name__ == "__main__":
    main()
