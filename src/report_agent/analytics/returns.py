"""Return calculations. Pure pandas: no database, so every function is easy to test.

Conventions used throughout:
- Inputs are daily simple returns (0.01 = +1%).
- Returns over a period are compounded (geometric), never added up.
- Periods longer than one year are annualized; shorter ones are not.
"""

from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cumulative_return(daily: pd.Series) -> float:
    """Compounded return: (1 + r1) x (1 + r2) x ... - 1."""
    return float(np.prod(1 + daily.to_numpy()) - 1) if len(daily) else 0.0


def annualized_return(daily: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Compounded return scaled to one year: (1 + cumulative) ^ (252 / days) - 1."""
    if len(daily) == 0:
        return 0.0
    return float((1 + cumulative_return(daily)) ** (periods_per_year / len(daily)) - 1)


def period_return(daily: pd.Series, annualize: bool) -> float:
    """Cumulative return, or annualized return when the period is longer than a year."""
    return annualized_return(daily) if annualize else cumulative_return(daily)


def is_longer_than_a_year(start: date, end: date) -> bool:
    """Measured in calendar days, so a 1-year period is never annualized."""
    return (end - start).days > 366


def standard_periods(end: date, start: date, inception: date) -> list[tuple[str, date]]:
    """The periods every performance table shows, as (label, first day)."""
    end_ts = pd.Timestamp(end)
    return [
        ("Period", start),
        ("Year to date", date(end.year, 1, 1)),
        ("1 year", (end_ts - pd.DateOffset(years=1) + pd.Timedelta(days=1)).date()),
        ("Since inception", inception),
    ]


def performance_table(returns: pd.DataFrame, start: date, end: date, inception: date) -> pd.DataFrame:
    """Portfolio, benchmark and active return for each standard period.

    returns: daily returns indexed by date with columns 'portfolio' and 'benchmark'.
    A period that begins before the data starts is shown as not available (NaN).
    """
    first_day = returns.index.min().date()
    rows = []
    for label, period_start in standard_periods(end, start, inception):
        window = returns.loc[pd.Timestamp(period_start):pd.Timestamp(end)]
        available = period_start >= first_day or label == "Since inception"
        if window.empty or not available:
            rows.append({"period": label, "start": period_start, "portfolio": np.nan,
                         "benchmark": np.nan, "active": np.nan, "annualized": False})
            continue
        annualize = is_longer_than_a_year(period_start, end)
        p = period_return(window["portfolio"], annualize)
        b = period_return(window["benchmark"], annualize)
        rows.append({
            "period": label,
            "start": max(period_start, first_day),
            "portfolio": p,
            "benchmark": b,
            "active": p - b,
            "annualized": annualize,
        })
    return pd.DataFrame(rows)


def cumulative_growth(daily: pd.Series, start_value: float = 100.0) -> pd.Series:
    """Growth of 100 over time, for the performance chart."""
    return start_value * (1 + daily).cumprod()
