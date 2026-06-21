"""
Trade-CSV loader — turns an uploaded file into a per-trading-day P&L series.

Built to swallow NinjaTrader "Trades" grid exports (Trade number, Instrument,
Account, Strategy, Market pos., Qty, Entry price, Exit price, Entry time,
Exit time, Profit, ...) and also generic two-column date,pnl files.

It is forgiving: it sniffs the P&L column (Profit / PnL / P&L / Net / Realized)
and a date column (Exit time / Entry time / Date / Time), parses money strings
like "$1,234.50" and "($45.00)" (parentheses = negative), and groups every
trade's realized P&L by calendar day.
"""

from __future__ import annotations

import io
import re
from typing import Dict, Tuple, List

import pandas as pd

_PNL_HINTS = ["profit", "pnl", "p&l", "p/l", "net", "realized", "realised", "gain"]
_DATE_HINTS = ["exit time", "exit", "close time", "date", "entry time", "time"]


def _money_to_float(v) -> float:
    """'$1,234.50' / '(45.00)' / '-45' -> float. Blank -> 0.0."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[()$,\s]", "", s)
    s = s.replace("%", "")
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def _pick(columns: List[str], hints: List[str]) -> str | None:
    low = {c: str(c).strip().lower() for c in columns}
    # exact-ish first, then contains
    for hint in hints:
        for c, cl in low.items():
            if cl == hint:
                return c
    for hint in hints:
        for c, cl in low.items():
            if hint in cl:
                return c
    return None


def load_trades(file_or_bytes) -> Tuple[Dict[str, float], pd.DataFrame, str]:
    """Parse a CSV into ({date_str: pnl}, preview_dataframe, note).

    Raises ValueError with a human message if no usable P&L column is found.
    """
    if isinstance(file_or_bytes, (bytes, bytearray)):
        raw = io.BytesIO(bytes(file_or_bytes))
    else:
        raw = file_or_bytes
    df = pd.read_csv(raw)
    df.columns = [str(c).strip() for c in df.columns]

    pnl_col = _pick(list(df.columns), _PNL_HINTS)
    if pnl_col is None:
        raise ValueError(
            "Could not find a profit/P&L column. Expected one of: "
            "Profit, PnL, P&L, Net, Realized. Columns seen: "
            + ", ".join(map(str, df.columns))
        )

    date_col = _pick(list(df.columns), _DATE_HINTS)
    pnl = df[pnl_col].map(_money_to_float)

    if date_col is not None:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        # rows with unparseable dates fall back to row order
        keys = [d.strftime("%Y-%m-%d") if pd.notna(d) else f"row-{i}"
                for i, d in enumerate(dates)]
        note = f"P&L from '{pnl_col}', grouped by day from '{date_col}'."
    else:
        # no date -> treat each trade as its own pseudo-day, preserving order
        keys = [f"t-{i:06d}" for i in range(len(pnl))]
        note = f"P&L from '{pnl_col}'; no date column, each trade treated as one day."

    by_day: Dict[str, float] = {}
    for k, v in zip(keys, pnl):
        by_day[k] = by_day.get(k, 0.0) + float(v)

    return by_day, df, note


def sample_trades(seed_days: int = 180) -> Tuple[Dict[str, float], pd.DataFrame, str]:
    """A deterministic ORB-style sample (no RNG) so the app works out of the box.

    Mostly-winning small days with the occasional larger green day and a few
    red days — enough to exercise audition pass/fail, consistency and payouts.
    """
    # a repeating pattern of daily $ P&L (per micro-contract-ish), tuned so the
    # default settings produce a believable mix of outcomes.
    pattern = [120, 80, -60, 150, 40, -110, 200, 60, 90, -40,
               130, 70, 110, -90, 180, 50, 100, -70, 160, 85,
               -130, 140, 75, 95, 210, -55, 120, 65, 105, -80]
    rows, by_day = [], {}
    base = pd.Timestamp("2024-01-02")
    for i in range(seed_days):
        pnl = float(pattern[i % len(pattern)])
        day = base + pd.Timedelta(days=i)
        # skip weekends to look like real trading days
        while day.weekday() >= 5:
            day += pd.Timedelta(days=1)
        ds = day.strftime("%Y-%m-%d")
        by_day[ds] = by_day.get(ds, 0.0) + pnl
        rows.append({
            "Trade number": i + 1,
            "Instrument": "MNQ JUN25",
            "Account": "Backtest",
            "Strategy": "ORB BOT V1.4",
            "Market pos.": "Long" if pnl >= 0 else "Short",
            "Qty": 1,
            "Entry price": 18000 + (i % 50),
            "Exit price": 18000 + (i % 50) + pnl / 2.0,
            "Entry time": (day + pd.Timedelta(hours=9, minutes=35)).strftime("%Y-%m-%d %H:%M"),
            "Exit time": (day + pd.Timedelta(hours=10, minutes=15)).strftime("%Y-%m-%d %H:%M"),
            "Profit": f"${pnl:,.2f}" if pnl >= 0 else f"(${abs(pnl):,.2f})",
        })
    return by_day, pd.DataFrame(rows), "Sample ORB strategy data (deterministic)."
