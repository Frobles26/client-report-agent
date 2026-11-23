"""Quality check tests. Each check gets small hand-made inputs with a known answer."""

import os
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from openpyxl import load_workbook

from report_agent.checks.quality import (
    FAIL,
    PASS,
    WARN,
    CheckResult,
    check_extreme_moves,
    check_files_match,
    check_freshness,
    check_prices_complete,
    check_returns_complete,
    check_stuck_runs,
    check_weights,
    format_results,
    overall,
)
from report_agent.outputs.deck import build_deck
from report_agent.outputs.excel import build_excel

DAYS = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]


def test_weights_must_add_to_one():
    ok = pd.DataFrame({"strategy_id": ["CAN_EQ"] * 2, "as_of_date": DAYS[:2],
                       "total_weight": [1.0, 0.99995]})
    assert check_weights(ok).status == PASS
    bad = ok.assign(total_weight=[1.0, 0.97])
    result = check_weights(bad)
    assert result.status == FAIL and "97.0000%" in result.detail


def test_freshness_counts_business_days():
    assert check_freshness(date(2026, 11, 20), date(2026, 11, 23)).status == PASS  # Fri -> Mon
    stale = check_freshness(date(2026, 11, 13), date(2026, 11, 23))
    assert stale.status == WARN and "6 business days" in stale.detail
    assert check_freshness(None, date(2026, 11, 23)).status == FAIL


def test_one_missing_day_warns_many_missing_days_fail():
    prices = pd.DataFrame({"ticker": ["RY.TO"] * 3 + ["TD.TO"] * 2,
                           "price_date": DAYS + DAYS[:2]})
    result = check_prices_complete(prices, ["RY.TO", "TD.TO"], DAYS)
    assert result.status == FAIL                      # 1 of 3 days = 33%, over the 5% limit
    assert "TD.TO missing 1 of 3 days (2026-09-30)" in result.detail

    calendar = list(pd.bdate_range("2026-07-01", "2026-09-30").date)        # 66 days
    prices = pd.DataFrame({"ticker": "RY.TO", "price_date": calendar[1:]})  # 1 missing = 1.5%
    assert check_prices_complete(prices, ["RY.TO"], calendar).status == WARN
    full = pd.DataFrame({"ticker": "RY.TO", "price_date": calendar})
    assert check_prices_complete(full, ["RY.TO"], calendar).status == PASS


def test_returns_complete():
    assert check_returns_complete(DAYS, DAYS).status == PASS
    result = check_returns_complete(DAYS[:2], DAYS)
    assert result.status == FAIL and result.detail == "2026-09-30"


def test_extreme_moves_warn():
    daily = pd.DataFrame({"return_date": DAYS, "portfolio_return": [0.01, -0.12, 0.0],
                          "benchmark_return": [0.0, 0.02, 0.15]})
    result = check_extreme_moves(daily)
    assert result.status == WARN
    assert "2026-09-29 portfolio -12.00%" in result.detail
    assert "2026-09-30 benchmark +15.00%" in result.detail


def test_stuck_runs_warn_after_an_hour():
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    running = pd.DataFrame({"run_id": [7, 8], "job_name": ["agent_report", "etl_prices"],
                            "started_at": [now - timedelta(hours=3), now - timedelta(minutes=5)]})
    result = check_stuck_runs(running, now)
    assert result.status == WARN and "run 7" in result.detail and "run 8" not in result.detail


def test_overall_and_format():
    results = [CheckResult("a", PASS, "ok"), CheckResult("b", WARN, "hmm")]
    assert overall(results) == WARN
    assert overall(results + [CheckResult("c", FAIL, "bad")]) == FAIL
    assert format_results(results).endswith("Overall: WARN")


# ---------- files match the analytics ----------

@pytest.fixture
def files(report, tmp_path):
    return build_excel(report, tmp_path / "r.xlsx"), build_deck(report, tmp_path / "r.pptx")


def test_files_match_report(report, files):
    result = check_files_match(report, *files)
    assert result.status == PASS
    assert result.detail == "Excel and PowerPoint match the analytics"


def test_edited_excel_value_is_caught(report, files):
    excel, deck = files
    wb = load_workbook(excel)
    wb["Performance"]["D5"] = 0.5                      # someone overtypes the period return
    wb.save(excel)
    result = check_files_match(report, excel, deck)
    assert result.status == FAIL and "Excel Performance Period" in result.detail


def test_stale_deck_is_caught(report, files, tmp_path):
    other = report.performance.copy()
    other.loc[0, "portfolio"] = 0.05                    # numbers changed after the deck was built
    changed = type(report)(**{**report.__dict__, "performance": other})
    result = check_files_match(changed, None, files[1])
    assert result.status == FAIL and "Deck returns Period: +1.20% vs +5.00%" in result.detail


def test_no_files_warns(report):
    assert check_files_match(report, None, None).status == WARN


# ---------- database (writes: CI only, or set RUN_DB_WRITE_TESTS=1) ----------

@pytest.mark.skipif(os.getenv("RUN_DB_WRITE_TESTS") != "1", reason="writes to the database")
def test_interrupted_run_is_marked_failed():
    from sqlalchemy import text

    from report_agent.db import get_engine
    from report_agent.etl.load import log_run

    engine = get_engine()
    with pytest.raises(KeyboardInterrupt), log_run(engine, "test_interrupt"):
        raise KeyboardInterrupt
    with engine.connect() as conn:
        status, message = conn.execute(text(
            "SELECT status, message FROM ops.runs WHERE job_name = 'test_interrupt' "
            "ORDER BY run_id DESC LIMIT 1")).one()
    assert status == "failed" and message == "KeyboardInterrupt"
