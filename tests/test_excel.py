"""Excel output tests. They build a workbook from made-up report data, so no database is needed."""

import numpy as np
import pytest
from openpyxl import load_workbook

from report_agent.outputs.excel import build_excel


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
