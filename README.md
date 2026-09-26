# Prop Firm Profit Farming Calculator

A Streamlit web app that replays a trading strategy's real trade history against
typical prop-firm evaluation + funded rules and estimates the economics of
farming payouts across many staggered accounts over 12 / 18 / 24 months.

It's a clean-room recreation of the calculator at `vinceretrading.com/simulator`,
built to embed in **ericbuilds.xyz**. The drawdown + consistency math mirrors the
live trading system's `prop_guard.py` / `firm_rules.py`.

## What's here

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — sidebar inputs, firm presets, rules box, 4 result tabs |
| `engine.py` | Pure simulation engine (no I/O) — audition/funded/payout model + cash-flow ledger |
| `data_loader.py` | CSV parser (NinjaTrader trade exports + generic) + sample data |
| `test_engine.py` | `pytest` suite (31 tests) |
| `samples/sample_trades.csv` | The built-in sample as a NinjaTrader-style CSV (column template) |
| `.streamlit/config.toml` | Dark/blue theme + iframe-embed settings |
| `.github/workflows/tests.yml` | CI: pytest + headless smoke test of the app |
| `requirements.txt` | `streamlit`, `pandas` |

## Run it locally

```bash
py -m pip install -r requirements.txt
py -m streamlit run app.py
# opens http://localhost:8501
```

Run the tests:

```bash
py -m pytest -q
```

## Deploy to Streamlit Community Cloud (free)

1. Push this repo to a **public GitHub repo** (e.g. `ericbuilds-simulator`).
2. Go to <https://share.streamlit.io> → sign in with GitHub → **New app**.
3. Pick the repo, branch `main`, main file `app.py` → **Deploy**.
4. You get a public URL like `https://ericbuilds-simulator.streamlit.app`.

## Embed it on ericbuilds.xyz

Drop this on whatever page you want the tool to live on (works on any platform —
Webflow, Wix, WordPress, plain HTML). This is exactly how the reference site does
it — their `/simulator` page is just an iframe.

```html
<iframe
  src="https://ericbuilds-simulator.streamlit.app/?embed=true"
  style="width:100%; height:100vh; border:0;"
  title="Prop Firm Profit Farming Calculator">
</iframe>
```

The `?embed=true` flag hides Streamlit's top toolbar/footer so it looks native.

## The simulation model (so you can tune it)

Every number in the sidebar feeds `engine.Params`. Results update live — there
is no "run" button, a full 12/18/24-month run takes well under a second.

- **Traders:** a new trader starts every trading day / week / month of the
  horizon and runs the strategy forward from a different offset into the trade
  history (cycled if the horizon is longer than the dataset). Each trader runs
  *Number of Accounts* identical accounts in parallel (copy-trading). Results
  are a deterministic spread — no RNG.
- **Audition:** accumulate daily P&L from 0. **Pass** at the profit target once
  the consistency rule (best day ≤ X% of net profit) and the minimum trading
  days are satisfied; **blow** if the balance hits the drawdown line → pay
  another challenge fee and retry until the runway ends.
- **Funded:** keep accumulating. The trailing drawdown applies until
  `+funded_dd_lock_profit`; after that it **locks at breakeven for good** and
  the account only fails if profit drops below $0 (the Apex/MFFU "threshold
  stops trailing" mechanic). Every time profit reaches the **payout trigger**
  (profit terms) with the minimum qualifying days and the funded consistency
  rule met, the payout amount is withdrawn and banked, then an approval wait
  passes with no trading. A funded blow-up ends the trader's run unless
  *Re-enter the audition* is on.
- **Drawdown types:** *End of Day* and *Trailing (Intraday)* are both evaluated
  on the daily P&L series (there is no intraday path in the data) and both
  lock at breakeven; *Static* is a fixed floor and never locks.
- **Daily loss limit:** flattens the day at −DLL (the firm closes you out), it
  does not blow the account.
- **Costs:** challenge fee per attempt (one-time) or per audition month
  (monthly); activation fee on each funding; VPS per trader for the months the
  trader is actually active. Per-account fees scale by Number of Accounts; VPS
  is one box per trader.
- **Net = payouts − costs. ROI = net / costs.** The *Monthly Profit Projection*
  tab is the month-by-month cash-flow ledger of the same simulation (payouts
  minus fees) for the whole staggered operation and for the first trader alone.

### Firm presets

The sidebar can pre-fill the rules for Apex, MFFU and TopStep 50K accounts.
These are **approximate public rules** — firms change them often, and some
mechanics (payout caps, safety nets, day-count rules) are simplified into the
knobs above. Always verify against the firm's current rule page; every field
stays editable after loading a preset.

## CSV format

Drop in a **NinjaTrader "Trades" grid export** (or any CSV with a profit column).
The loader sniffs the delimiter (`,` `;` tab `|`), the P&L column (`Profit` /
`PnL` / `P&L` / `Net` / `Realized` — cumulative columns are ignored) and a date
column (`Exit time` / `Entry time` / `Date`), parses money strings like
`$1,234.50`, `($45.00)` and `1.234,50`, and groups realized P&L by calendar day.
Tick *Subtract Commission column* if your export's Profit is gross of
commissions. `samples/sample_trades.csv` shows the expected layout.
