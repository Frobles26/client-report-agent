"""Risk statistics from daily returns. Pure pandas, annualized with 252 trading days."""

import numpy as np
import pandas as pd

from report_agent.analytics.returns import TRADING_DAYS

SQRT_DAYS = np.sqrt(TRADING_DAYS)


def volatility(daily: pd.Series) -> float:
    """Annualized standard deviation of daily returns."""
    return float(daily.std(ddof=1) * SQRT_DAYS)


def sharpe_ratio(daily: pd.Series, risk_free_daily: pd.Series) -> float:
    """Average excess return over the risk-free rate, per unit of volatility (annualized)."""
    excess = daily - risk_free_daily.reindex(daily.index).fillna(0.0)
    vol = excess.std(ddof=1)
    return float(excess.mean() / vol * SQRT_DAYS) if vol > 0 else float("nan")


def tracking_error(portfolio: pd.Series, benchmark: pd.Series) -> float:
    """How much the portfolio's return differs from the benchmark's (annualized std of the gap)."""
    return float((portfolio - benchmark).std(ddof=1) * SQRT_DAYS)


def information_ratio(portfolio: pd.Series, benchmark: pd.Series) -> float:
    """Average active return per unit of tracking error (annualized)."""
    active = portfolio - benchmark
    te = active.std(ddof=1)
    return float(active.mean() / te * SQRT_DAYS) if te > 0 else float("nan")


def max_drawdown(daily: pd.Series) -> float:
    """Largest fall from a previous peak in cumulative value (a negative number)."""
    wealth = (1 + daily).cumprod()
    peak = np.maximum(wealth.cummax(), 1.0)  # the starting value of 1 counts as a peak
    return float((wealth / peak - 1).min())


def beta(portfolio: pd.Series, benchmark: pd.Series) -> float:
    """Sensitivity to the benchmark: covariance / benchmark variance."""
    var = benchmark.var(ddof=1)
    return float(portfolio.cov(benchmark) / var) if var > 0 else float("nan")


def risk_table(returns: pd.DataFrame, risk_free_daily: pd.Series) -> pd.DataFrame:
    """All risk statistics for portfolio and benchmark in one table.

    Pass at least a year of daily returns: statistics from a few weeks of data are noisy.
    """
    p, b = returns["portfolio"], returns["benchmark"]
    rows = [
        ("Volatility", volatility(p), volatility(b)),
        ("Sharpe ratio", sharpe_ratio(p, risk_free_daily), sharpe_ratio(b, risk_free_daily)),
        ("Max drawdown", max_drawdown(p), max_drawdown(b)),
        ("Beta", beta(p, b), 1.0),
        ("Tracking error", tracking_error(p, b), np.nan),
        ("Information ratio", information_ratio(p, b), np.nan),
    ]
    return pd.DataFrame(rows, columns=["statistic", "portfolio", "benchmark"])
