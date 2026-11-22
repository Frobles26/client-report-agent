"""Gather everything a report needs, once. The summary, Excel and PowerPoint outputs all use it."""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from report_agent.analytics import data, risk, weights
from report_agent.analytics.returns import performance_table
from report_agent.db import get_engine


@dataclass
class ReportData:
    strategy_id: str
    strategy_name: str
    benchmark_ticker: str
    benchmark_name: str
    inception: date
    start: date
    end: date
    risk_start: date
    performance: pd.DataFrame    # period, start, portfolio, benchmark, active, annualized
    risk: pd.DataFrame           # statistic, portfolio, benchmark
    sectors: pd.DataFrame        # sector, weight, holdings
    top10: pd.DataFrame          # ticker, name, sector, weight, period_return, contribution
    daily: pd.DataFrame          # daily portfolio and benchmark returns for the period
    holdings_date: date          # the month-end the holdings come from

    @property
    def period_label(self) -> str:
        return f"{self.start:%b %d, %Y} to {self.end:%b %d, %Y}"


def build_report_data(strategy_id: str, start: date, end: date) -> ReportData:
    if start > end:
        raise ValueError(f"Start date {start} is after end date {end}.")
    engine = get_engine()
    strategy = data.get_strategy(engine, strategy_id)
    inception = strategy["inception_date"]
    history = data.get_daily_returns(engine, strategy_id, inception, end)
    if history.empty:
        raise ValueError("No returns found. Run: python -m report_agent.etl.run_pipeline")

    # Risk is measured over the year ending on `end` (or since inception if shorter):
    # a single quarter has too few days for stable risk statistics.
    risk_start = max(pd.Timestamp(end) - pd.DateOffset(years=1), pd.Timestamp(inception))
    risk_window = history.loc[risk_start + pd.Timedelta(days=1):pd.Timestamp(end)]
    rf = data.get_risk_free(engine, risk_window.index)

    holdings = data.get_holdings(engine, strategy_id, start)
    if holdings.empty:
        raise ValueError(f"No holdings on or before {start}.")
    stock_returns = data.get_stock_returns(engine, holdings["ticker"].tolist(), start, end)

    return ReportData(
        strategy_id=strategy_id,
        strategy_name=strategy["name"],
        benchmark_ticker=strategy["benchmark_ticker"],
        benchmark_name=strategy["benchmark_name"],
        inception=inception,
        start=start,
        end=end,
        risk_start=(risk_start + pd.Timedelta(days=1)).date(),
        performance=performance_table(history, start, end, inception),
        risk=risk.risk_table(risk_window, rf),
        sectors=weights.sector_weights(holdings),
        top10=weights.top_holdings(holdings, stock_returns),
        daily=history.loc[pd.Timestamp(start):pd.Timestamp(end)],
        holdings_date=holdings["as_of_date"].iloc[0],
    )
