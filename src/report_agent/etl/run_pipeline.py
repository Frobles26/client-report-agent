"""The daily data pipeline: extract -> validate -> load, with every step logged in ops.runs.

Run: python -m report_agent.etl.run_pipeline
     python -m report_agent.etl.run_pipeline --start 2024-01-01   (backfill from a date)
"""

import argparse
import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from report_agent.config import BENCHMARK_TICKER, BOC_SERIES, HISTORY_START
from report_agent.db import get_engine
from report_agent.etl import extract, load
from report_agent.etl.validate import validate_prices

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "sample"
log = logging.getLogger("pipeline")


def read_reference() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    securities = pd.read_csv(DATA_DIR / "securities.csv")
    strategies = pd.read_csv(DATA_DIR / "strategies.csv", parse_dates=["inception_date"])
    strategies["inception_date"] = strategies["inception_date"].dt.date
    holdings_path = DATA_DIR / "holdings.csv"
    if not holdings_path.exists():
        raise SystemExit("data/sample/holdings.csv is missing. Run: python -m report_agent.etl.make_holdings")
    holdings = pd.read_csv(holdings_path, parse_dates=["as_of_date"])
    holdings["as_of_date"] = holdings["as_of_date"].dt.date
    return securities, strategies, holdings


def run(start: str, price_fetcher=extract.fetch_prices, boc_fetcher=extract.fetch_boc_series) -> None:
    """Run every step. The fetchers can be swapped for fakes in tests."""
    engine = get_engine()
    securities, strategies, holdings = read_reference()

    with load.log_run(engine, "etl_reference") as info, engine.begin() as conn:
        info["rows_loaded"] = load.load_reference(conn, securities, strategies)
        info["rows_loaded"] += load.load_holdings(conn, holdings)
    log.info("Reference data and holdings loaded (%s rows)", info["rows_loaded"])

    with load.log_run(engine, "etl_boc") as info:
        frames = [boc_fetcher(series_id, start) for series_id in BOC_SERIES]
        boc = pd.concat(frames, ignore_index=True)
        with engine.begin() as conn:
            info["rows_loaded"] = load.load_boc(conn, boc)
    log.info("Bank of Canada series loaded (%s rows)", info["rows_loaded"])

    with load.log_run(engine, "etl_prices") as info:
        raw = price_fetcher(securities["ticker"].tolist(), start)
        clean, report = validate_prices(raw, calendar_ticker=BENCHMARK_TICKER)
        clean = clean.merge(securities[["ticker", "currency"]], on="ticker", how="inner")
        with engine.begin() as conn:
            loaded = load.load_prices(conn, clean)
            staged = load.build_staging_prices(conn)
        info["rows_loaded"] = loaded
        info["message"] = report.summary()
    log.info("Prices loaded: %s; staging rows written: %s", report.summary(), staged)
    for item in report.big_moves[:10]:
        log.warning("Big one-day move to review: %s", item)
    for ticker, days in report.missing_days.items():
        log.warning("%s is missing %s trading days", ticker, days)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load prices, rates and the portfolio into PostgreSQL.")
    parser.add_argument("--start", default=None, help="First date to load (YYYY-MM-DD). "
                        f"Default: {HISTORY_START} on the first run, otherwise the last 10 days.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start = args.start or default_start()
    log.info("Starting pipeline from %s", start)
    run(start)
    log.info("Pipeline finished")


def default_start() -> str:
    """Full history on the first run; afterwards reload only the last 10 days (cheap and safe)."""
    from sqlalchemy import text
    with get_engine().connect() as conn:
        latest = conn.execute(text("SELECT MAX(price_date) FROM raw.prices")).scalar()
    if latest is None:
        return HISTORY_START
    return str(latest - timedelta(days=10))


if __name__ == "__main__":
    main()
