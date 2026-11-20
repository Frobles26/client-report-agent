"""Schema tests. They need a running database, so they skip when there is none."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from report_agent.db import get_engine, missing_tables


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    try:
        with eng.connect():
            pass
    except OperationalError:
        pytest.skip("No database available; start it with `docker compose up -d`.")
    return eng


def test_all_tables_exist(engine):
    assert missing_tables(engine) == set()


def test_prices_reject_non_positive_close(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO raw.prices (ticker, price_date, close, source) "
                    "VALUES ('TEST.TO', '2024-01-02', -1, 'test')"
                )
            )
        trans.rollback()


def test_weight_totals_view(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text(
            "INSERT INTO dim.securities VALUES "
            "('^TEST', 'Test index', 'Index', 'CAD', TRUE), "
            "('AAA.TO', 'Stock A', 'Financials', 'CAD', FALSE), "
            "('BBB.TO', 'Stock B', 'Energy', 'CAD', FALSE)"
        ))
        conn.execute(text(
            "INSERT INTO dim.strategies VALUES ('TEST', 'Test strategy', '^TEST', '2024-01-01', 'CAD')"
        ))
        conn.execute(text(
            "INSERT INTO staging.holdings VALUES "
            "('TEST', 'AAA.TO', '2024-01-31', 0.55), ('TEST', 'BBB.TO', '2024-01-31', 0.45)"
        ))
        total = conn.execute(text(
            "SELECT total_weight FROM staging.v_weight_totals WHERE strategy_id = 'TEST'"
        )).scalar_one()
        trans.rollback()
    assert float(total) == pytest.approx(1.0)
