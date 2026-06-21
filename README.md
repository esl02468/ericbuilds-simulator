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
| `app.py` | Streamlit UI — sidebar inputs, rules box, 4 result tabs |
| `engine.py` | Pure simulation engine (no I/O) — audition/funded/payout model |
| `data_loader.py` | CSV parser (NinjaTrader trade exports + generic) + sample data |
| `test_engine.py` | `pytest` suite (11 tests) |
| `.streamlit/config.toml` | Dark/blue theme + iframe-embed settings |
| `requirements.txt` | `streamlit`, `pandas` |

## Run it locally

```bash
py -m pip install -r requirements.txt
py -m streamlit run app.py
# opens http://localhost:8501
```

Run the tests:

```bash
py -m pytest test_engine.py -q
```

## Deploy to Streamlit Community Cloud (free)

1. Push this `simulator/` folder to a **public GitHub repo** (e.g. `ericbuilds-simulator`).
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

Every number in the sidebar feeds `engine.Params`. The model:

- **Audition:** accumulate daily P&L from 0. **Pass** at the profit target if the
  consistency rule is satisfiable; **blow** if the balance hits the trailing
  drawdown line → pay another challenge fee and retry until the runway ends.
- **Funded:** keep accumulating. The trailing drawdown applies until
  `+funded_dd_lock_profit`, after which only a negative balance fails the account
  (the Apex/MFFU "locks at breakeven" mechanic). Each time the balance reaches the
  payout trigger you bank a payout, gated by minimum qualifying days + an approval
  wait.
- **Costs:** challenge fee per attempt (or monthly), activation fee on funding,
  VPS monthly while active. Per-account fees scale by Number of Accounts; VPS is
  one box.
- **Net = payouts − costs. ROI = net / costs.** Each "trader" starts at a different
  offset into the trade history, so results are a deterministic spread — no RNG.

These are documented assumptions, not the reference site's exact internals. The
defaults match the rules shown on that page; adjust any field to match the
specific firm (Apex, MFFU, TopStep…) you're modelling.

## CSV format

Drop in a **NinjaTrader "Trades" grid export** (or any CSV with a profit column).
The loader sniffs the P&L column (`Profit` / `PnL` / `P&L` / `Net` / `Realized`)
and a date column (`Exit time` / `Entry time` / `Date`), parses money strings like
`$1,234.50` and `($45.00)`, and groups realized P&L by day.
