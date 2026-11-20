"""Database connection and a quick health check.

Run `python -m report_agent.db` to confirm PostgreSQL is up and the schema is loaded.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from report_agent.config import DATABASE_URL

EXPECTED_TABLES = {
    "raw.prices",
    "raw.boc_series",
    "dim.securities",
    "dim.strategies",
    "staging.holdings",
    "staging.daily_prices",
    "fact.strategy_returns",
    "ops.runs",
}


def get_engine(url: str = DATABASE_URL) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def missing_tables(engine: Engine) -> set[str]:
    """Return the expected tables that do not exist yet."""
    query = text(
        """
        SELECT table_schema || '.' || table_name
        FROM information_schema.tables
        WHERE table_schema IN ('raw', 'dim', 'staging', 'fact', 'ops')
        """
    )
    with engine.connect() as conn:
        found = {row[0] for row in conn.execute(query)}
    return EXPECTED_TABLES - found


def main() -> None:
    engine = get_engine()
    try:
        missing = missing_tables(engine)
    except Exception as exc:  # connection refused, wrong password, etc.
        raise SystemExit(f"Could not connect to the database: {exc}") from exc
    if missing:
        raise SystemExit(f"Connected, but these tables are missing: {sorted(missing)}")
    print(f"Database OK: all {len(EXPECTED_TABLES)} tables found.")


if __name__ == "__main__":
    main()
