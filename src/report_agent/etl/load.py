"""Load: write to PostgreSQL. Every write is an upsert, so loads can be re-run safely."""

from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

UPSERT_RAW_PRICES = text("""
    INSERT INTO raw.prices (ticker, price_date, close, currency, source)
    VALUES (:ticker, :price_date, :close, :currency, :source)
    ON CONFLICT (ticker, price_date) DO UPDATE
    SET close = EXCLUDED.close, currency = EXCLUDED.currency,
        source = EXCLUDED.source, loaded_at = now()
""")

UPSERT_BOC = text("""
    INSERT INTO raw.boc_series (series_id, obs_date, value)
    VALUES (:series_id, :obs_date, :value)
    ON CONFLICT (series_id, obs_date) DO UPDATE
    SET value = EXCLUDED.value, loaded_at = now()
""")

UPSERT_SECURITIES = text("""
    INSERT INTO dim.securities (ticker, name, sector, currency, is_index)
    VALUES (:ticker, :name, :sector, :currency, :is_index)
    ON CONFLICT (ticker) DO UPDATE
    SET name = EXCLUDED.name, sector = EXCLUDED.sector,
        currency = EXCLUDED.currency, is_index = EXCLUDED.is_index
""")

UPSERT_STRATEGIES = text("""
    INSERT INTO dim.strategies (strategy_id, name, benchmark_ticker, inception_date, base_currency)
    VALUES (:strategy_id, :name, :benchmark_ticker, :inception_date, :base_currency)
    ON CONFLICT (strategy_id) DO UPDATE
    SET name = EXCLUDED.name, benchmark_ticker = EXCLUDED.benchmark_ticker,
        inception_date = EXCLUDED.inception_date, base_currency = EXCLUDED.base_currency
""")

UPSERT_HOLDINGS = text("""
    INSERT INTO staging.holdings (strategy_id, ticker, as_of_date, weight)
    VALUES (:strategy_id, :ticker, :as_of_date, :weight)
    ON CONFLICT (strategy_id, ticker, as_of_date) DO UPDATE
    SET weight = EXCLUDED.weight
""")

# raw.prices -> staging.daily_prices: convert to CAD and calculate the daily return.
# USD prices use the latest Bank of Canada USD/CAD rate on or before that day.
BUILD_STAGING_PRICES = text("""
    INSERT INTO staging.daily_prices (ticker, price_date, close_cad, daily_return)
    SELECT ticker, price_date, close_cad,
           close_cad / LAG(close_cad) OVER (PARTITION BY ticker ORDER BY price_date) - 1
    FROM (
        SELECT p.ticker, p.price_date,
               CASE WHEN p.currency = 'CAD' THEN p.close ELSE p.close * fx.value END AS close_cad
        FROM raw.prices p
        JOIN dim.securities s ON s.ticker = p.ticker
        LEFT JOIN LATERAL (
            SELECT b.value FROM raw.boc_series b
            WHERE b.series_id = 'FX' || p.currency || 'CAD' AND b.obs_date <= p.price_date
            ORDER BY b.obs_date DESC LIMIT 1
        ) fx ON p.currency <> 'CAD'
    ) converted
    WHERE close_cad IS NOT NULL
    ON CONFLICT (ticker, price_date) DO UPDATE
    SET close_cad = EXCLUDED.close_cad, daily_return = EXCLUDED.daily_return
""")


def _records(df: pd.DataFrame) -> list[dict]:
    return df.to_dict(orient="records")


def upsert(conn: Connection, statement, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn.execute(statement, _records(df))
    return len(df)


def load_prices(conn: Connection, prices: pd.DataFrame, source: str = "yahoo") -> int:
    df = prices.assign(currency=prices.get("currency", "CAD"), source=source)
    return upsert(conn, UPSERT_RAW_PRICES, df[["ticker", "price_date", "close", "currency", "source"]])


def load_boc(conn: Connection, series: pd.DataFrame) -> int:
    return upsert(conn, UPSERT_BOC, series)


def load_reference(conn: Connection, securities: pd.DataFrame, strategies: pd.DataFrame) -> int:
    securities = securities.assign(is_index=securities["is_index"].astype(bool))
    return upsert(conn, UPSERT_SECURITIES, securities) + upsert(conn, UPSERT_STRATEGIES, strategies)


def load_holdings(conn: Connection, holdings: pd.DataFrame) -> int:
    return upsert(conn, UPSERT_HOLDINGS, holdings)


def build_staging_prices(conn: Connection) -> int:
    return conn.execute(BUILD_STAGING_PRICES).rowcount


@contextmanager
def log_run(engine: Engine, job_name: str) -> Iterator[dict]:
    """Record a run in ops.runs: 'running' at the start, then 'completed' or 'failed'.

    The caller puts the row count in the yielded dict: info["rows_loaded"] = n
    """
    with engine.begin() as conn:
        run_id = conn.execute(
            text("INSERT INTO ops.runs (job_name) VALUES (:job) RETURNING run_id"), {"job": job_name}
        ).scalar_one()
    info: dict = {"rows_loaded": None, "message": None}
    try:
        yield info
    except BaseException as exc:      # includes Ctrl+C, so an interrupted run never stays 'running'
        message = str(exc) or type(exc).__name__
        _finish_run(engine, run_id, "failed", info.get("rows_loaded"), message[:1000])
        raise
    _finish_run(engine, run_id, "completed", info.get("rows_loaded"), info.get("message"))


def _finish_run(engine: Engine, run_id: int, status: str, rows: int | None, message: str | None):
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE ops.runs
                SET finished_at = now(), status = :status, rows_loaded = :rows, message = :message
                WHERE run_id = :run_id
            """),
            {"status": status, "rows": rows, "message": message, "run_id": run_id},
        )
