"""Tests for the data pipeline. None of them call the internet."""

import os
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from report_agent.etl import extract, make_holdings
from report_agent.etl.validate import validate_prices

# ---------- extract ----------

BOC_SAMPLE = {
    "seriesDetail": {"FXUSDCAD": {"label": "USD/CAD"}},
    "observations": [
        {"d": "2024-01-02", "FXUSDCAD": {"v": "1.3316"}},
        {"d": "2024-01-03", "FXUSDCAD": {"v": ""}},          # gap: skipped
        {"d": "2024-01-04", "FXUSDCAD": {"v": "1.3354"}},
    ],
}


def test_parse_boc_json_skips_gaps():
    df = extract.parse_boc_json(BOC_SAMPLE, "FXUSDCAD")
    assert list(df["value"]) == [1.3316, 1.3354]
    assert df["obs_date"].iloc[0] == date(2024, 1, 2)


def test_parse_boc_json_rejects_wrong_series():
    with pytest.raises(extract.ExtractError):
        extract.parse_boc_json(BOC_SAMPLE, "TB.CDN.90D.MID")


def test_close_to_long_handles_yfinance_layout():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    cols = pd.MultiIndex.from_product([["Close", "Open"], ["RY.TO", "TD.TO"]])
    wide = pd.DataFrame(np.arange(8, dtype=float).reshape(2, 4), index=idx, columns=cols)
    wide.loc[idx[1], ("Close", "TD.TO")] = np.nan
    long = extract._close_to_long(wide, ["RY.TO", "TD.TO"])
    assert len(long) == 3  # the missing TD close is dropped
    assert set(long.columns) == {"ticker", "price_date", "close"}


# ---------- validate ----------

def test_validate_prices():
    d = [date(2024, 1, day) for day in (2, 3, 4, 5)]
    prices = pd.DataFrame({
        "ticker": ["^IDX"] * 4 + ["AAA"] * 3 + ["AAA", "BBB"],
        "price_date": d + [d[0], d[1], d[3]] + [d[3], d[0]],
        "close": [100, 101, 102, 103, 10, 10.5, 20, 20, -5],  # AAA doubles on the 5th, BBB negative
    })
    clean, report = validate_prices(prices, calendar_ticker="^IDX")
    assert report.dropped_missing_or_bad == 1          # BBB negative price
    assert report.dropped_duplicates == 1              # AAA on the 5th twice
    assert report.big_moves == ["AAA 2024-01-05"]      # 10.5 -> 20 is +90%
    assert report.missing_days == {"AAA": 1}           # AAA has no price on the 4th
    assert len(clean) == 7


# ---------- holdings ----------

def test_holdings_sum_to_one_every_month():
    dates = make_holdings.month_ends("2024-01-01", date(2024, 7, 15))
    assert dates[0] == date(2023, 12, 31) and dates[-1] == date(2024, 6, 30)
    h = make_holdings.make_holdings([f"T{i}" for i in range(40)], dates)
    totals = h.groupby("as_of_date")["weight"].sum()
    assert np.allclose(totals, 1.0, atol=1e-9)
    assert (h["weight"] > 0).all()


def test_holdings_are_reproducible():
    dates = make_holdings.month_ends("2024-01-01", date(2024, 4, 1))
    a = make_holdings.make_holdings(["A", "B", "C"], dates)
    b = make_holdings.make_holdings(["A", "B", "C"], dates)
    pd.testing.assert_frame_equal(a, b)


# ---------- full pipeline (writes to the database: CI only, or set RUN_DB_WRITE_TESTS=1) ----------

def fake_prices(tickers, start, end=None):
    days = pd.bdate_range(start, "2024-03-29")
    rng = np.random.default_rng(0)
    rows = []
    for t in tickers:
        path = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))
        rows += [{"ticker": t, "price_date": d.date(), "close": float(p)} for d, p in zip(days, path)]
    return pd.DataFrame(rows)


def fake_boc(series_id, start):
    days = pd.bdate_range(start, "2024-03-29")
    value = 1.35 if series_id.startswith("FX") else 5.0
    return pd.DataFrame({"series_id": series_id, "obs_date": [d.date() for d in days], "value": value})


@pytest.mark.skipif(os.getenv("RUN_DB_WRITE_TESTS") != "1", reason="writes to the database")
def test_pipeline_end_to_end_and_rerun_is_safe():
    from report_agent.db import get_engine
    from report_agent.etl.run_pipeline import run

    run("2024-01-01", price_fetcher=fake_prices, boc_fetcher=fake_boc)
    engine = get_engine()
    count_sql = text("SELECT (SELECT COUNT(*) FROM raw.prices), (SELECT COUNT(*) FROM staging.daily_prices)")
    with engine.connect() as conn:
        first = conn.execute(count_sql).one()

    run("2024-01-01", price_fetcher=fake_prices, boc_fetcher=fake_boc)  # second run: no duplicates
    with engine.connect() as conn:
        second = conn.execute(count_sql).one()
        nulls = conn.execute(text(
            "SELECT COUNT(*) FROM staging.daily_prices WHERE daily_return IS NULL"
        )).scalar_one()
        tickers = conn.execute(text("SELECT COUNT(*) FROM dim.securities")).scalar_one()
        statuses = conn.execute(text(
            "SELECT DISTINCT status FROM ops.runs WHERE job_name LIKE 'etl_%'"
        )).scalars().all()

    assert first == second and first[0] > 0
    assert nulls == tickers          # only each ticker's first day has no return
    assert statuses == ["completed"]
