"""
Trade-CSV loader — turns an uploaded file into a per-trading-day P&L series.

Built to swallow NinjaTrader "Trades" grid exports (Trade number, Instrument,
Account, Strategy, Market pos., Qty, Entry price, Exit price, Entry time,
Exit time, Profit, Cum. net profit, Commission, ...) and also generic
two-column date,pnl files.

It is forgiving: it sniffs the delimiter, the P&L column (Profit / PnL / P&L /
Net / Realized — never a cumulative column) and a date column (Exit time /
Entry time / Date / Time), parses money strings like "$1,234.50" and
"($45.00)" (parentheses = negative), and groups every trade's realized P&L by
calendar day.
"""

from __future__ import annotations

import io
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

_PNL_HINTS = ["profit", "pnl", "p&l", "p/l", "net", "realized", "realised", "gain"]
_PNL_EXCLUDE = ["cum", "cumulative", "running", "total", "%", "percent", "mae", "mfe"]
_DATE_HINTS = ["exit time", "exit", "close time", "closed", "date", "entry time", "time"]
_COMMISSION_HINTS = ["commission", "commissions", "fees"]


def _money_to_float(v) -> float:
    """'$1,234.50' / '(45.00)' / '-45' / '1.234,50' -> float. Blank -> 0.0."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return 0.0 if pd.isna(v) else float(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return 0.0
    neg = (s.startswith("(") and s.endswith(")")) or "-" in s
    s = re.sub(r"[()$€£\s%+-]", "", s)
    s = s.replace("USD", "").replace("usd", "")
    # European "1.234,50" -> "1234.50"; otherwise drop thousands separators
    if "," in s and "." in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s and re.fullmatch(r"\d{1,3}(,\d{3})+", s) is None:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def _pick(columns: List[str], hints: List[str],
          exclude: Optional[List[str]] = None) -> Optional[str]:
    low = {c: str(c).strip().lower() for c in columns}
    if exclude:
        low = {c: cl for c, cl in low.items() if not any(x in cl for x in exclude)}
    # exact match first, then contains
    for hint in hints:
        for c, cl in low.items():
            if cl == hint:
                return c
    for hint in hints:
        for c, cl in low.items():
            if hint in cl:
                return c
    return None


def _read_csv(file_or_bytes) -> pd.DataFrame:
    """Read a CSV from bytes / path / file-like, sniffing the delimiter and
    tolerating a UTF-8 BOM and odd encodings."""
    if isinstance(file_or_bytes, (bytes, bytearray)):
        data = bytes(file_or_bytes)
    elif hasattr(file_or_bytes, "read"):
        data = file_or_bytes.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
    else:
        with open(file_or_bytes, "rb") as fh:
            data = fh.read()

    sep = _sniff_delimiter(data)
    last_err: Optional[Exception] = None
    for kwargs in ({"sep": sep}, {"sep": sep, "engine": "python", "on_bad_lines": "skip"}):
        try:
            df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig",
                             encoding_errors="replace", skip_blank_lines=True, **kwargs)
            if df.shape[1] >= 1:
                return df
        except Exception as e:  # noqa: BLE001 — try the next strategy
            last_err = e
    raise ValueError(f"Could not parse the file as CSV ({last_err}).")


def _sniff_delimiter(data: bytes) -> str:
    """Pick the delimiter that appears most in the header line (default ',')."""
    header = data.lstrip(b"\xef\xbb\xbf").split(b"\n", 1)[0].decode("utf-8", "replace")
    counts = {d: header.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def load_trades(file_or_bytes, subtract_commission: bool = False
                ) -> Tuple[Dict[str, float], pd.DataFrame, str]:
    """Parse a CSV into ({date_str: pnl}, preview_dataframe, note).

    Raises ValueError with a human message if no usable P&L column is found.
    """
    df = _read_csv(file_or_bytes)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    pnl_col = _pick(list(df.columns), _PNL_HINTS, exclude=_PNL_EXCLUDE)
    if pnl_col is None:
        raise ValueError(
            "Could not find a profit/P&L column. Expected one of: "
            "Profit, PnL, P&L, Net, Realized. Columns seen: "
            + ", ".join(map(str, df.columns))
        )

    pnl = df[pnl_col].map(_money_to_float).astype(float)
    notes = [f"P&L from '{pnl_col}'"]

    if subtract_commission:
        com_col = _pick(list(df.columns), _COMMISSION_HINTS)
        if com_col is not None:
            pnl = pnl - df[com_col].map(_money_to_float).abs().astype(float)
            notes.append(f"minus '{com_col}'")

    if pnl.abs().sum() == 0:
        raise ValueError(f"Column '{pnl_col}' parsed to all zeros — check the export "
                         "shows P&L in currency (not ticks/points/percent).")

    date_col = _pick(list(df.columns), _DATE_HINTS)
    if date_col is not None:
        dates = _parse_dates(df[date_col])
        if dates.notna().sum() == 0:
            date_col = None

    if date_col is not None:
        # rows with unparseable dates fall back to row order (kept, not dropped)
        keys = [d.strftime("%Y-%m-%d") if pd.notna(d) else f"zz-row-{i:06d}"
                for i, d in enumerate(dates)]
        notes.append(f"grouped by day from '{date_col}'")
    else:
        # no date -> treat each trade as its own pseudo-day, preserving order
        keys = [f"t-{i:06d}" for i in range(len(pnl))]
        notes.append("no date column, each trade treated as one day")

    by_day: Dict[str, float] = {}
    for k, v in zip(keys, pnl):
        by_day[k] = by_day.get(k, 0.0) + float(v)

    return by_day, df, "; ".join(notes) + "."


def _parse_dates(col: pd.Series) -> pd.Series:
    """Parse a date/time column, tolerating mixed formats."""
    try:
        parsed = pd.to_datetime(col, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(col, errors="coerce")
    if parsed.notna().sum() == 0:
        # maybe day-first (e.g. 31/12/2024)
        try:
            parsed = pd.to_datetime(col, errors="coerce", format="mixed", dayfirst=True)
        except (TypeError, ValueError):
            pass
    return parsed


# a repeating pattern of daily $ P&L (per micro-contract-ish), tuned so the
# default settings produce a believable mix of outcomes.
_SAMPLE_PATTERN = [120, 80, -60, 150, 40, -110, 200, 60, 90, -40,
                   130, 70, 110, -90, 180, 50, 100, -70, 160, 85,
                   -130, 140, 75, 95, 210, -55, 120, 65, 105, -80]


def sample_trades(seed_days: int = 180) -> Tuple[Dict[str, float], pd.DataFrame, str]:
    """A deterministic ORB-style sample (no RNG) so the app works out of the box.

    Exactly `seed_days` trading days (weekends skipped), one trade per day:
    mostly-winning small days with the occasional larger green day and a few
    red days — enough to exercise audition pass/fail, consistency and payouts.
    """
    days = pd.bdate_range("2024-01-02", periods=seed_days)
    rows, by_day = [], {}
    for i, day in enumerate(days):
        pnl = float(_SAMPLE_PATTERN[i % len(_SAMPLE_PATTERN)])
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
            "Cum. net profit": f"${sum(by_day.values()):,.2f}",
            "Commission": "$1.24",
        })
    return by_day, pd.DataFrame(rows), "Sample ORB strategy data (deterministic)."
