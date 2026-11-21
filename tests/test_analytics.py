"""Analytics tests. Each expected value is worked out by hand in the comment beside it,
so anyone can check the maths with a calculator or Excel."""

import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

from report_agent.analytics import returns, risk, weights

SQRT252 = np.sqrt(252)


def s(values):
    return pd.Series(values, index=pd.bdate_range("2026-01-05", periods=len(values)), dtype=float)


# ---------- returns ----------

def test_cumulative_return_compounds():
    # 1.10 x 0.95 x 1.02 = 1.0659  ->  +6.59% (adding them up would wrongly give +7%)
    assert returns.cumulative_return(s([0.10, -0.05, 0.02])) == pytest.approx(0.0659)


def test_annualized_return():
    # two days of +1% = 1.0201; with 4 periods a year: 1.0201 ^ (4/2) - 1 = 0.04060401
    assert returns.annualized_return(s([0.01, 0.01]), periods_per_year=4) == pytest.approx(0.04060401)


def test_one_year_is_not_annualized_but_two_years_are():
    assert not returns.is_longer_than_a_year(date(2025, 10, 1), date(2026, 9, 30))
    assert returns.is_longer_than_a_year(date(2024, 1, 1), date(2026, 9, 30))


def test_performance_table_periods():
    idx = pd.bdate_range("2024-01-02", "2026-09-30")
    df = pd.DataFrame({"portfolio": 0.0004, "benchmark": 0.0003}, index=idx)
    table = returns.performance_table(
        df, start=date(2026, 7, 1), end=date(2026, 9, 30), inception=date(2024, 1, 1)
    ).set_index("period")
    q3_days = len(pd.bdate_range("2026-07-01", "2026-09-30"))
    assert table.loc["Period", "portfolio"] == pytest.approx(1.0004 ** q3_days - 1)
    assert table.loc["Period", "active"] == pytest.approx(1.0004 ** q3_days - 1.0003 ** q3_days)
    assert not table.loc["1 year", "annualized"]
    assert table.loc["Since inception", "annualized"]
    assert table.loc["Since inception", "portfolio"] == pytest.approx(1.0004 ** 252 - 1)
    assert len(table) == 4


def test_period_before_data_starts_is_blank():
    idx = pd.bdate_range("2026-03-02", "2026-09-30")
    df = pd.DataFrame({"portfolio": 0.001, "benchmark": 0.001}, index=idx)
    table = returns.performance_table(df, date(2026, 7, 1), date(2026, 9, 30), date(2026, 3, 1))
    assert np.isnan(table.set_index("period").loc["1 year", "portfolio"])


# ---------- risk ----------

def test_volatility():
    # mean 0; squared gaps 4 x 0.0001; / (4-1) -> variance 0.000133; std 0.011547; x sqrt(252)
    assert risk.volatility(s([0.01, -0.01, 0.01, -0.01])) == pytest.approx(0.011547005 * SQRT252)


def test_sharpe_ratio():
    # excess = returns (risk-free 0); mean 0.015; std 0.0129099; 0.015 / 0.0129099 x sqrt(252) = 18.4447
    daily = s([0.02, 0.00, 0.01, 0.03])
    assert risk.sharpe_ratio(daily, s([0, 0, 0, 0])) == pytest.approx(18.4447, rel=1e-4)


def test_sharpe_subtracts_risk_free():
    daily = s([0.02, 0.00, 0.01, 0.03])
    lower = risk.sharpe_ratio(daily, s([0.001] * 4))
    assert lower == pytest.approx(0.014 / 0.01290994 * SQRT252, rel=1e-4)


def test_max_drawdown():
    # value 1.10 -> 0.88 -> 0.924: the worst fall from the 1.10 peak is 0.88 / 1.10 - 1 = -20%
    assert risk.max_drawdown(s([0.10, -0.20, 0.05])) == pytest.approx(-0.20)
    # a loss on day one counts from the starting value of 1
    assert risk.max_drawdown(s([-0.10, 0.05])) == pytest.approx(-0.10)


def test_beta_and_tracking_error():
    bench = s([0.01, -0.02, 0.015, 0.005])
    assert risk.beta(bench * 2, bench) == pytest.approx(2.0)           # moves twice as much
    assert risk.tracking_error(bench, bench) == pytest.approx(0.0)     # identical returns


def test_information_ratio():
    # active returns 0.015, -0.005, 0.015, -0.005: mean 0.005, std 0.011547 -> 0.4330 x sqrt(252)
    bench = s([0.0, 0.0, 0.0, 0.0])
    port = s([0.015, -0.005, 0.015, -0.005])
    assert risk.information_ratio(port, bench) == pytest.approx(0.005 / 0.011547005 * SQRT252)


def test_risk_table_has_every_statistic():
    df = pd.DataFrame({"portfolio": [0.01, -0.005, 0.007], "benchmark": [0.008, -0.004, 0.006]},
                      index=pd.bdate_range("2026-01-05", periods=3))
    table = risk.risk_table(df, pd.Series(0.0, index=df.index))
    assert list(table["statistic"]) == [
        "Volatility", "Sharpe ratio", "Max drawdown",
        "Beta", "Tracking error", "Information ratio",
    ]


# ---------- weights ----------

HOLDINGS = pd.DataFrame({
    "ticker": ["A", "B", "C", "D"],
    "name": ["Alpha", "Beta", "Gamma", "Delta"],
    "sector": ["Energy", "Financials", "Energy", "Utilities"],
    "weight": [0.40, 0.30, 0.20, 0.10],
})


def test_sector_weights():
    table = weights.sector_weights(HOLDINGS).set_index("sector")
    assert table.loc["Energy", "weight"] == pytest.approx(0.60)  # 0.40 + 0.20
    assert table.loc["Energy", "holdings"] == 2
    assert table["weight"].sum() == pytest.approx(1.0)


def test_top_holdings_contribution():
    period = pd.Series({"A": 0.10, "B": -0.05, "C": 0.0, "D": 0.20})
    top = weights.top_holdings(HOLDINGS, period, n=2)
    assert list(top["ticker"]) == ["A", "B"]
    assert top.loc[0, "contribution"] == pytest.approx(0.04)    # 40% x +10%
    assert top.loc[1, "contribution"] == pytest.approx(-0.015)  # 30% x -5%


# ---------- fact table in the database (CI only, or RUN_DB_WRITE_TESTS=1) ----------

@pytest.mark.skipif(os.getenv("RUN_DB_WRITE_TESTS") != "1", reason="writes to the database")
def test_fact_returns_match_a_manual_calculation():
    from sqlalchemy import text

    from report_agent.analytics import data
    from report_agent.db import get_engine
    from report_agent.etl.run_pipeline import run
    from tests.test_etl import fake_boc, fake_prices

    run("2024-01-01", price_fetcher=fake_prices, boc_fetcher=fake_boc)
    engine = get_engine()
    day = date(2024, 2, 15)
    with engine.connect() as conn:
        manual = conn.execute(text("""
            SELECT SUM(h.weight * p.daily_return)
            FROM staging.holdings h
            JOIN staging.daily_prices p ON p.ticker = h.ticker AND p.price_date = :day
            WHERE h.strategy_id = 'CAN_EQ' AND h.as_of_date = '2024-01-31'
        """), {"day": day}).scalar_one()
    stored = data.get_daily_returns(engine, "CAN_EQ", day, day)
    # fact.strategy_returns stores 8 decimals (NUMERIC(12, 8)), so compare to 8 decimals
    assert stored.loc[pd.Timestamp(day), "portfolio"] == pytest.approx(float(manual), abs=1e-8)
