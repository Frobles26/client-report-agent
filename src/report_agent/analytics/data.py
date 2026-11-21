"""Read and write the analytics data in PostgreSQL. The maths lives in returns.py and risk.py."""

from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

RISK_FREE_SERIES = "TB.CDN.90D.MID"

# Portfolio return for a day = sum of (weight x stock return), using the month-end weights
# dated before that day (staging.v_daily_weights). If a stock has no price that day, the
# remaining weights are scaled up so they still add to 100% (SUM(weight) in the divisor).
BUILD_FACT_RETURNS = text("""
    INSERT INTO fact.strategy_returns (strategy_id, return_date, portfolio_return, benchmark_return)
    SELECT w.strategy_id,
           w.price_date,
           SUM(w.weight * p.daily_return) / NULLIF(SUM(w.weight), 0) AS portfolio_return,
           b.daily_return AS benchmark_return
    FROM staging.v_daily_weights w
    JOIN staging.daily_prices p
      ON p.ticker = w.ticker AND p.price_date = w.price_date AND p.daily_return IS NOT NULL
    JOIN dim.strategies s ON s.strategy_id = w.strategy_id
    JOIN staging.daily_prices b
      ON b.ticker = s.benchmark_ticker AND b.price_date = w.price_date AND b.daily_return IS NOT NULL
    WHERE w.strategy_id = :strategy_id
    GROUP BY w.strategy_id, w.price_date, b.daily_return
    ON CONFLICT (strategy_id, return_date) DO UPDATE
    SET portfolio_return = EXCLUDED.portfolio_return,
        benchmark_return = EXCLUDED.benchmark_return,
        calculated_at = now()
""")


def build_fact_returns(engine: Engine, strategy_id: str) -> int:
    """Recalculate every daily return for a strategy. Safe to re-run."""
    with engine.begin() as conn:
        return conn.execute(BUILD_FACT_RETURNS, {"strategy_id": strategy_id}).rowcount


def get_strategy(engine: Engine, strategy_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT s.strategy_id, s.name, s.benchmark_ticker, b.name AS benchmark_name,
                       s.inception_date, s.base_currency
                FROM dim.strategies s JOIN dim.securities b ON b.ticker = s.benchmark_ticker
                WHERE s.strategy_id = :sid
            """),
            {"sid": strategy_id},
        ).mappings().one_or_none()
    if row is None:
        raise ValueError(f"Unknown strategy '{strategy_id}'.")
    return dict(row)


def get_daily_returns(engine: Engine, strategy_id: str, start: date, end: date) -> pd.DataFrame:
    """Daily portfolio and benchmark returns between two dates (inclusive), indexed by date."""
    query = text("""
        SELECT return_date, portfolio_return, benchmark_return
        FROM fact.strategy_returns
        WHERE strategy_id = :sid AND return_date BETWEEN :start AND :end
        ORDER BY return_date
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": strategy_id, "start": start, "end": end})
    df = df.set_index(pd.to_datetime(df.pop("return_date")))
    return df.rename(columns={"portfolio_return": "portfolio", "benchmark_return": "benchmark"}).astype(float)


def get_risk_free(engine: Engine, dates: pd.DatetimeIndex) -> pd.Series:
    """Daily risk-free return from the 3-month T-bill yield (annual %, so / 100 / 252).

    The yield for each date is the latest one published on or before it.
    """
    if len(dates) == 0:
        return pd.Series(dtype=float)
    query = text("""
        SELECT obs_date, value FROM raw.boc_series
        WHERE series_id = :sid AND obs_date <= :end
        ORDER BY obs_date
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": RISK_FREE_SERIES, "end": dates.max().date()})
    if df.empty:
        raise ValueError(f"No risk-free data ({RISK_FREE_SERIES}). Run the pipeline first.")
    yields = df.set_index(pd.to_datetime(df["obs_date"]))["value"].astype(float)
    aligned = yields.reindex(yields.index.union(dates)).ffill().reindex(dates)
    return aligned / 100 / 252


def get_holdings(engine: Engine, strategy_id: str, as_of: date) -> pd.DataFrame:
    """Latest month-end holdings on or before `as_of`, with name and sector."""
    query = text("""
        SELECT h.ticker, s.name, s.sector, h.weight, h.as_of_date
        FROM staging.holdings h
        JOIN dim.securities s ON s.ticker = h.ticker
        WHERE h.strategy_id = :sid
          AND h.as_of_date = (SELECT MAX(as_of_date) FROM staging.holdings
                              WHERE strategy_id = :sid AND as_of_date <= :as_of)
        ORDER BY h.weight DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": strategy_id, "as_of": as_of})
    df["weight"] = df["weight"].astype(float)
    return df


def get_stock_returns(engine: Engine, tickers: list[str], start: date, end: date) -> pd.Series:
    """Each stock's total price return over the period, compounded from daily returns."""
    query = text("""
        SELECT ticker, EXP(SUM(LN(1 + daily_return))) - 1 AS period_return
        FROM staging.daily_prices
        WHERE ticker = ANY(:tickers) AND price_date BETWEEN :start AND :end
          AND daily_return IS NOT NULL
        GROUP BY ticker
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"tickers": tickers, "start": start, "end": end})
    return df.set_index("ticker")["period_return"].astype(float)
