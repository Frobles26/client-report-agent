"""Excel output tests. They build a workbook from made-up report data, so no database is needed."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from report_agent.analytics.report import ReportData
from report_agent.outputs.excel import build_excel


@pytest.fixture
def report() -> ReportData:
    days = pd.bdate_range("2026-07-01", "2026-09-30")
    rng = np.random.default_rng(3)
    daily = pd.DataFrame({"portfolio": rng.normal(0.0005, 0.008, len(days)),
                          "benchmark": rng.normal(0.0004, 0.009, len(days))}, index=days)
    return ReportData(
        strategy_id="CAN_EQ", strategy_name="Canadian Equity",
        benchmark_ticker="^GSPTSE", benchmark_name="S&P/TSX Composite Index",
        inception=date(2024, 1, 1), start=date(2026, 7, 1), end=date(2026, 9, 30),
        risk_start=date(2025, 10, 1),
        performance=pd.DataFrame({
            "period": ["Period", "Year to date", "1 year", "Since inception"],
            "start": [date(2026, 7, 1), date(2026, 1, 1), date(2025, 10, 1), date(2024, 1, 2)],
            "portfolio": [0.012, 0.08, np.nan, 0.10],          # a blank period must not break
            "benchmark": [0.010, 0.07, 0.09, 0.11],
            "active": [0.002, 0.01, np.nan, -0.01],
            "annualized": [False, False, False, True],
        }),
        risk=pd.DataFrame({
            "statistic": ["Volatility", "Sharpe ratio", "Max drawdown", "Beta",
                          "Tracking error", "Information ratio"],
            "portfolio": [0.12, 0.9, -0.08, 0.95, 0.03, 0.4],
            "benchmark": [0.13, 0.8, -0.09, 1.0, np.nan, np.nan],
        }),
        sectors=pd.DataFrame({"sector": ["Financials", "Energy"], "weight": [0.6, 0.4],
                              "holdings": [3, 2]}),
        top10=pd.DataFrame({"ticker": ["RY.TO", "ENB.TO"], "name": ["Royal Bank", "Enbridge"],
                            "sector": ["Financials", "Energy"], "weight": [0.3, 0.2],
                            "period_return": [0.05, -0.02], "contribution": [0.015, -0.004]}),
        daily=daily,
        holdings_date=date(2026, 6, 30),
    )


@pytest.fixture
def workbook(report, tmp_path):
    return load_workbook(build_excel(report, tmp_path / "report.xlsx"))


def test_tabs_in_order(workbook):
    assert workbook.sheetnames == ["Summary", "Performance", "Risk", "Sectors", "Top 10", "Daily", "Notes"]


def test_active_return_is_a_formula(workbook):
    ws = workbook["Performance"]
    assert ws["A5"].value == "Period"
    assert ws["F5"].value == '=IF(OR(D5="",E5=""),"",D5-E5)'
    assert ws["D7"].value is None  # the NaN period is written as an empty cell


def test_summary_links_to_detail_tabs(workbook):
    ws = workbook["Summary"]
    assert ws["B11"].value == "=Performance!D5"   # portfolio return for the period
    assert ws["C13"].value == "=Risk!C5"          # benchmark volatility


def test_totals_and_contributions_are_formulas(workbook):
    sectors, top = workbook["Sectors"], workbook["Top 10"]
    assert sectors["B7"].value == "=SUM(B5:B6)"
    assert top["F5"].value == '=IF(E5="","",D5*E5)'
    assert top["F7"].value == "=SUM(F5:F6)"


def test_growth_of_100_chains_daily_returns(workbook, report):
    ws = workbook["Daily"]
    assert ws["D2"].value == "=100*(1+B2)"
    assert ws["D3"].value == "=D2*(1+B3)"
    assert ws.max_row == len(report.daily) + 1


def test_charts_present(workbook):
    assert len(workbook["Performance"]._charts) == 1
    assert len(workbook["Sectors"]._charts) == 1


def test_no_nan_written_anywhere(workbook):
    for ws in workbook.worksheets:
        for row in ws.iter_rows(values_only=True):
            assert not any(isinstance(v, float) and np.isnan(v) for v in row)
