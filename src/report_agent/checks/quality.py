"""Quality checks: run before a report is trusted.

Data checks (read the database):
  1. weights_sum_to_one   every month-end holdings set adds up to 100%
  2. data_is_fresh        the latest price is recent
  3. prices_complete      every holding has a price on every benchmark trading day
  4. returns_complete     the fact table has a row for every benchmark trading day
  5. no_extreme_moves     no daily portfolio or benchmark move beyond 10%
  6. no_stuck_runs        no job left on 'running' in ops.runs
Output check (reads the files):
  7. files_match_report   Excel and PowerPoint show the same numbers as the analytics

Each check returns pass, warn or fail. A fail means the report should not go out.
The checks are pure functions on DataFrames, so tests need no database; the load_*
functions below fetch their inputs.

Run: python -m report_agent.checks.quality --start 2026-07-01 --end 2026-09-30
     add --fix-stuck to mark stuck runs as failed
"""

import argparse
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from pptx import Presentation
from sqlalchemy import text
from sqlalchemy.engine import Engine

PASS, WARN, FAIL = "pass", "warn", "fail"
WEIGHT_TOLERANCE = 0.0001        # 0.01%: weights are stored with 6 decimals
STALE_AFTER_BUSINESS_DAYS = 3
MISSING_PRICE_FAIL_SHARE = 0.05  # one ticker missing over 5% of days = fail; less = warn
EXTREME_MOVE = 0.10
STUCK_AFTER = timedelta(hours=1)


@dataclass
class CheckResult:
    name: str
    status: str                  # pass / warn / fail
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def _result(name: str, problems: list[str], ok: str, status_if_problem: str = FAIL,
            limit: int = 5) -> CheckResult:
    if not problems:
        return CheckResult(name, PASS, ok)
    more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
    return CheckResult(name, status_if_problem, "; ".join(problems[:limit]) + more)


# ---------- 1-6: data checks (pure functions) ----------

def check_weights(totals: pd.DataFrame) -> CheckResult:
    """totals: strategy_id, as_of_date, total_weight."""
    bad = totals[(totals["total_weight"].astype(float) - 1).abs() > WEIGHT_TOLERANCE]
    problems = [f"{r.strategy_id} {r.as_of_date}: {float(r.total_weight):.4%}"
                for r in bad.itertuples()]
    return _result("weights_sum_to_one", problems,
                   f"{len(totals)} holdings sets each add up to 100%")


def check_freshness(latest: date | None, today: date) -> CheckResult:
    if latest is None:
        return CheckResult("data_is_fresh", FAIL, "No prices loaded.")
    age = int(np.busday_count(latest, today))
    if age > STALE_AFTER_BUSINESS_DAYS:
        return CheckResult("data_is_fresh", WARN,
                           f"Latest price is {latest} ({age} business days old). "
                           "Run the data pipeline.")
    return CheckResult("data_is_fresh", PASS, f"Latest price is {latest}.")


def check_prices_complete(prices: pd.DataFrame, tickers: list[str],
                          calendar: list[date]) -> CheckResult:
    """prices: ticker, price_date. calendar: the benchmark's trading days in the period."""
    if not calendar:
        return CheckResult("prices_complete", FAIL, "No benchmark trading days in the period.")
    have = prices.groupby("ticker")["price_date"].apply(set).to_dict()
    days = set(calendar)
    problems, worst = [], 0.0
    for ticker in sorted(tickers):
        missing = sorted(days - have.get(ticker, set()))
        if missing:
            share = len(missing) / len(days)
            worst = max(worst, share)
            shown = ", ".join(str(d) for d in missing[:3]) + ("..." if len(missing) > 3 else "")
            problems.append(f"{ticker} missing {len(missing)} of {len(days)} days ({shown})")
    status = FAIL if worst > MISSING_PRICE_FAIL_SHARE else WARN
    return _result("prices_complete", problems,
                   f"{len(tickers)} holdings priced on all {len(days)} trading days", status)


def check_returns_complete(fact_dates: list[date], calendar: list[date]) -> CheckResult:
    missing = sorted(set(calendar) - set(fact_dates))
    return _result("returns_complete", [str(d) for d in missing],
                   f"Strategy returns on all {len(calendar)} trading days")


def check_extreme_moves(daily: pd.DataFrame) -> CheckResult:
    """daily: return_date, portfolio_return, benchmark_return."""
    problems = []
    for r in daily.itertuples():
        for label, value in (("portfolio", r.portfolio_return), ("benchmark", r.benchmark_return)):
            if abs(float(value)) > EXTREME_MOVE:
                problems.append(f"{r.return_date} {label} {float(value):+.2%}")
    return _result("no_extreme_moves", problems,
                   f"No daily move beyond {EXTREME_MOVE:.0%}", WARN)


def check_stuck_runs(running: pd.DataFrame, now: datetime) -> CheckResult:
    """running: run_id, job_name, started_at for runs with status 'running'."""
    stuck = running[running["started_at"] < now - STUCK_AFTER]
    problems = [f"run {r.run_id} {r.job_name} since {r.started_at:%Y-%m-%d %H:%M}"
                for r in stuck.itertuples()]
    if problems:
        problems[-1] += ". Fix with --fix-stuck"
    return _result("no_stuck_runs", problems, "No stuck runs", WARN)


# ---------- 7: output check ----------

def _close(a, b, tol=1e-9) -> bool:
    a_missing = a is None or (isinstance(a, float) and math.isnan(a))
    b_missing = b is None or (isinstance(b, float) and math.isnan(b))
    if a_missing or b_missing:
        return a_missing and b_missing
    return abs(float(a) - float(b)) <= tol


def check_files_match(report, excel_path: Path | None, deck_path: Path | None) -> CheckResult:
    """Compare the numbers in the Excel and PowerPoint files with the analytics."""
    from report_agent.outputs.deck import RATIO_STATS, pct, ratio

    problems, checked = [], []
    if excel_path and Path(excel_path).exists():
        wb = load_workbook(excel_path)               # values typed in (blue), not formulas
        perf, risk = wb["Performance"], wb["Risk"]
        for i, r in enumerate(report.performance.itertuples()):
            row = 5 + i
            for col, want in ((4, r.portfolio), (5, r.benchmark)):
                got = perf.cell(row=row, column=col).value
                if perf.cell(row=row, column=1).value != r.period or not _close(got, want):
                    problems.append(f"Excel Performance {r.period}: {got} vs {want}")
        for i, r in enumerate(report.risk.itertuples()):
            row = 5 + i
            for col, want in ((2, r.portfolio), (3, r.benchmark)):
                got = risk.cell(row=row, column=col).value
                if risk.cell(row=row, column=1).value != r.statistic or not _close(got, want):
                    problems.append(f"Excel Risk {r.statistic}: {got} vs {want}")
        checked.append("Excel")

    if deck_path and Path(deck_path).exists():
        slides = Presentation(deck_path).slides
        periods = next(s.table for s in slides[2].shapes if s.has_table)
        for i, r in enumerate(report.performance.itertuples(), start=1):
            for col, want in ((2, pct(r.portfolio, True)), (3, pct(r.benchmark, True)),
                              (4, pct(r.active, True))):
                got = periods.cell(i, col).text
                if got != want:
                    problems.append(f"Deck returns {r.period}: {got} vs {want}")
        risk_table = next(s.table for s in slides[3].shapes if s.has_table)
        for i, r in enumerate(report.risk.itertuples(), start=1):
            fmt = ratio if r.statistic in RATIO_STATS else pct
            for col, want in ((1, fmt(r.portfolio)), (2, fmt(r.benchmark))):
                got = risk_table.cell(i, col).text
                if got != want:
                    problems.append(f"Deck risk {r.statistic}: {got} vs {want}")
        checked.append("PowerPoint")

    if not checked:
        return CheckResult("files_match_report", WARN, "No files found to check.")
    return _result("files_match_report", problems,
                   f"{' and '.join(checked)} match the analytics")


# ---------- loading inputs from the database ----------

def load_data_inputs(engine: Engine, strategy_id: str, start: date, end: date) -> dict:
    with engine.connect() as conn:
        bench = conn.execute(text(
            "SELECT benchmark_ticker FROM dim.strategies WHERE strategy_id = :s"),
            {"s": strategy_id}).scalar_one()
        totals = pd.read_sql(text("""
            SELECT strategy_id, as_of_date, SUM(weight) AS total_weight
            FROM staging.holdings WHERE strategy_id = :s GROUP BY 1, 2 ORDER BY 2"""),
            conn, params={"s": strategy_id})
        latest = conn.execute(text(
            "SELECT MAX(price_date) FROM staging.daily_prices WHERE ticker = :b"),
            {"b": bench}).scalar()
        calendar = conn.execute(text("""
            SELECT price_date FROM staging.daily_prices
            WHERE ticker = :b AND price_date BETWEEN :start AND :end
              AND daily_return IS NOT NULL      -- the first day of history has no return
            ORDER BY 1"""),
            {"b": bench, "start": start, "end": end}).scalars().all()
        # holdings in effect during the period: the sets dated before the period's last day
        tickers = conn.execute(text("""
            SELECT DISTINCT ticker FROM staging.holdings
            WHERE strategy_id = :s AND weight > 0
              AND as_of_date >= (SELECT MAX(as_of_date) FROM staging.holdings
                                 WHERE strategy_id = :s AND as_of_date < :start)
              AND as_of_date < :end"""),
            {"s": strategy_id, "start": start, "end": end}).scalars().all()
        prices = pd.read_sql(text("""
            SELECT ticker, price_date FROM staging.daily_prices
            WHERE price_date BETWEEN :start AND :end AND ticker = ANY(:t)"""),
            conn, params={"start": start, "end": end, "t": list(tickers)})
        daily = pd.read_sql(text("""
            SELECT return_date, portfolio_return, benchmark_return FROM fact.strategy_returns
            WHERE strategy_id = :s AND return_date BETWEEN :start AND :end ORDER BY 1"""),
            conn, params={"s": strategy_id, "start": start, "end": end})
        running = pd.read_sql(text(
            "SELECT run_id, job_name, started_at FROM ops.runs WHERE status = 'running'"), conn)
    return {"totals": totals, "latest": latest, "calendar": list(calendar),
            "tickers": list(tickers), "prices": prices, "daily": daily, "running": running}


def run_data_checks(inputs: dict, today: date, now: datetime) -> list[CheckResult]:
    return [
        check_weights(inputs["totals"]),
        check_freshness(inputs["latest"], today),
        check_prices_complete(inputs["prices"], inputs["tickers"], inputs["calendar"]),
        check_returns_complete(list(inputs["daily"]["return_date"]), inputs["calendar"]),
        check_extreme_moves(inputs["daily"]),
        check_stuck_runs(inputs["running"], now),
    ]


def data_checks(strategy_id: str, start: date, end: date, run_id: int | None = None) -> list[CheckResult]:
    """Load from the database and run checks 1-6. run_id: the caller's own run, not 'stuck'."""
    from report_agent.db import get_engine
    inputs = load_data_inputs(get_engine(), strategy_id, start, end)
    if run_id is not None:
        inputs["running"] = inputs["running"][inputs["running"]["run_id"] != run_id]
    return run_data_checks(inputs, date.today(), datetime.now(UTC))


def fix_stuck_runs(engine: Engine) -> int:
    """Mark runs left on 'running' for over an hour as failed. Returns how many."""
    with engine.begin() as conn:
        return conn.execute(text("""
            UPDATE ops.runs SET status = 'failed', finished_at = now(),
                   message = COALESCE(message, 'Marked failed: stuck on running (interrupted).')
            WHERE status = 'running' AND started_at < now() - interval '1 hour'""")).rowcount


def overall(results: list[CheckResult]) -> str:
    statuses = {r.status for r in results}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS


def format_results(results: list[CheckResult]) -> str:
    icon = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}
    width = max(len(r.name) for r in results)
    lines = [f"{icon[r.status]}  {r.name:<{width}}  {r.detail}" for r in results]
    return "\n".join(lines + [f"\nOverall: {overall(results).upper()}"])


def main() -> None:
    from report_agent.analytics.report import build_report_data
    from report_agent.db import get_engine
    from report_agent.outputs.excel import OUTPUT_DIR

    parser = argparse.ArgumentParser(description="Run the quality checks for a report.")
    parser.add_argument("--strategy", default="CAN_EQ")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--fix-stuck", action="store_true", help="mark stuck runs as failed")
    args = parser.parse_args()

    if args.fix_stuck:
        print(f"Marked {fix_stuck_runs(get_engine())} stuck run(s) as failed.\n")
    results = data_checks(args.strategy, args.start, args.end)
    stem = OUTPUT_DIR / f"{args.strategy}_{args.start}_{args.end}"
    report = build_report_data(args.strategy, args.start, args.end)
    results.append(check_files_match(report, stem.with_suffix(".xlsx"), stem.with_suffix(".pptx")))
    print(format_results(results))
    if overall(results) == FAIL:
        raise SystemExit(1)                    # non-zero exit: a scheduler or CI sees the failure


if __name__ == "__main__":
    main()
