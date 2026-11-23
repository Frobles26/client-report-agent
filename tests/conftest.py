"""Shared test data: a made-up report, so output tests need no database."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from report_agent.analytics.report import ReportData


@pytest.fixture
def report() -> ReportData:
    days = pd.bdate_range("2026-07-01", "2026-09-30")
    rng = np.random.default_rng(3)
    daily = pd.DataFrame({"portfolio": rng.normal(0.0005, 0.008, len(days)),
                          "benchmark": rng.normal(0.0004, 0.009, len(days))}, index=days)
    return ReportData(
        strategy_id="CAN_EQ", strategy_name="Canadian Equity",
        benchmark_ticker="^GSPTSE", benchmark_name="S&P/TSX Composite Index",
        inception=date(2024, 1, 1), start=date(2026, 7, 1), end=date(2026, 9, 30),
        risk_start=date(2025, 10, 1),
        performance=pd.DataFrame({
            "period": ["Period", "Year to date", "1 year", "Since inception"],
            "start": [date(2026, 7, 1), date(2026, 1, 1), date(2025, 10, 1), date(2024, 1, 2)],
            "portfolio": [0.012, 0.08, np.nan, 0.10],          # a blank period must not break
            "benchmark": [0.010, 0.07, 0.09, 0.11],
            "active": [0.002, 0.01, np.nan, -0.01],
            "annualized": [False, False, False, True],
        }),
        risk=pd.DataFrame({
            "statistic": ["Volatility", "Sharpe ratio", "Max drawdown", "Beta",
                          "Tracking error", "Information ratio"],
            "portfolio": [0.12, 0.9, -0.08, 0.95, 0.03, 0.4],
            "benchmark": [0.13, 0.8, -0.09, 1.0, np.nan, np.nan],
        }),
        sectors=pd.DataFrame({"sector": ["Financials", "Energy"], "weight": [0.6, 0.4],
                              "holdings": [3, 2]}),
        top10=pd.DataFrame({"ticker": ["RY.TO", "ENB.TO"], "name": ["Royal Bank", "Enbridge"],
                            "sector": ["Financials", "Energy"], "weight": [0.3, 0.2],
                            "period_return": [0.05, -0.02], "contribution": [0.015, -0.004]}),
        daily=daily,
        holdings_date=date(2026, 6, 30),
    )
