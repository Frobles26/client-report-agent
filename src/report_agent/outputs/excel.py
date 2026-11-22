"""Build the formatted Excel workbook for a report.

Tabs: Summary, Performance, Risk, Sectors, Top 10, Daily, Notes.
Totals, active returns, contributions and growth of 100 are live Excel formulas, so the
workbook recalculates if a number is changed. Font colours follow the usual finance
convention: blue = data typed in, black = formula, green = link to another tab.

Run: python -m report_agent.outputs.excel --start 2026-07-01 --end 2026-09-30
"""

import argparse
import math
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from report_agent.analytics.report import ReportData, build_report_data

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"

FONT = "Arial"
NAVY = "1F3A5F"
GREY = "8C96A3"
INPUT_BLUE = "0000FF"
LINK_GREEN = "008000"

PCT = "0.00%;-0.00%;-"
RATIO = "0.00;-0.00"
DATE_FMT = "yyyy-mm-dd"
INDEX_FMT = "0.00"

TITLE = Font(name=FONT, size=14, bold=True, color=NAVY)
SUBTITLE = Font(name=FONT, size=10, color="595959")
HEADER = Font(name=FONT, size=10, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
BODY = Font(name=FONT, size=10)
DATA = Font(name=FONT, size=10, color=INPUT_BLUE)
LINK = Font(name=FONT, size=10, color=LINK_GREEN)
BOLD = Font(name=FONT, size=10, bold=True)
TOTAL_BORDER = Border(top=Side(style="thin", color="000000"))

RATIO_STATS = {"Sharpe ratio", "Beta", "Information ratio"}


# ---------- helpers ----------

def _blank(value):
    """Excel has no NaN: write missing numbers as empty cells."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _title(ws: Worksheet, title: str, subtitle: str) -> None:
    ws["A1"] = title
    ws["A1"].font = TITLE
    ws["A2"] = subtitle
    ws["A2"].font = SUBTITLE


def _header(ws: Worksheet, row: int, labels: list[str]) -> None:
    for col, label in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=col, value=label)
        cell.font = HEADER
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center" if col > 1 else "left", vertical="center")


def _put(ws: Worksheet, row: int, col: int, value, fmt: str | None = None, font: Font = DATA):
    cell = ws.cell(row=row, column=col, value=_blank(value))
    cell.font = font
    if fmt:
        cell.number_format = fmt
    return cell


def _widths(ws: Worksheet, widths: list[float]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _clean_chart(chart) -> None:
    """Settings that stop Excel drawing titles over the plot or hiding the axes."""
    chart.roundedCorners = False
    if chart.legend is not None:
        chart.legend.overlay = False
        chart.legend.position = "b"
    if hasattr(chart, "x_axis"):
        chart.x_axis.delete = False
        chart.y_axis.delete = False


# ---------- tabs ----------

def _daily_sheet(wb: Workbook, report: ReportData) -> int:
    """Daily returns (data) and growth of 100 (formulas). Returns the last data row."""
    ws = wb.create_sheet("Daily")
    _header(ws, 1, ["Date", "Portfolio return", "Benchmark return",
                    "Portfolio growth of 100", "Benchmark growth of 100"])
    row = 1
    for row, (day, values) in enumerate(report.daily.iterrows(), start=2):
        _put(ws, row, 1, day.date(), DATE_FMT)
        _put(ws, row, 2, float(values["portfolio"]), "0.0000%")
        _put(ws, row, 3, float(values["benchmark"]), "0.0000%")
        if row == 2:
            ws.cell(row=row, column=4, value="=100*(1+B2)")
            ws.cell(row=row, column=5, value="=100*(1+C2)")
        else:
            ws.cell(row=row, column=4, value=f"=D{row - 1}*(1+B{row})")
            ws.cell(row=row, column=5, value=f"=E{row - 1}*(1+C{row})")
        for col in (4, 5):
            ws.cell(row=row, column=col).font = BODY
            ws.cell(row=row, column=col).number_format = INDEX_FMT
    ws.freeze_panes = "A2"
    _widths(ws, [12, 17, 17, 22, 22])
    return row


def _performance_sheet(wb: Workbook, report: ReportData, last_daily_row: int) -> None:
    ws = wb.create_sheet("Performance")
    _title(ws, "Performance", f"{report.strategy_name} vs. {report.benchmark_name}")
    _header(ws, 4, ["Period", "Start", "End", "Portfolio", "Benchmark", "Active", "Annualized"])
    for i, rec in enumerate(report.performance.itertuples(index=False)):
        r = 5 + i
        _put(ws, r, 1, rec.period, font=BODY)
        _put(ws, r, 2, rec.start, DATE_FMT)
        _put(ws, r, 3, report.end, DATE_FMT)
        _put(ws, r, 4, rec.portfolio, PCT)
        _put(ws, r, 5, rec.benchmark, PCT)
        active = ws.cell(row=r, column=6, value=f'=IF(OR(D{r}="",E{r}=""),"",D{r}-E{r})')
        active.font, active.number_format = BODY, PCT
        flag = _put(ws, r, 7, "Yes" if rec.annualized else "No", font=BODY)
        flag.alignment = Alignment(horizontal="center")
    note = 5 + len(report.performance) + 1
    ws.cell(row=note, column=1,
            value="Periods longer than one year are annualized. Returns exclude dividends.").font = SUBTITLE

    if last_daily_row >= 3:
        chart = LineChart()
        chart.title = "Growth of 100 over the period"
        chart.y_axis.title = "Value"
        chart.y_axis.number_format = "0"
        chart.x_axis.number_format = "mmm d"
        # Zoom the value axis to the data (a 0-based axis would flatten the lines).
        growth = 100 * (1 + report.daily).cumprod()
        low, high = float(growth.min().min()), float(growth.max().max())
        chart.y_axis.scaling.min = math.floor(min(low, 100) / 5) * 5
        chart.y_axis.scaling.max = math.ceil(max(high, 100) / 5) * 5
        chart.height, chart.width = 8, 18
        values = Reference(wb["Daily"], min_col=4, max_col=5, min_row=1, max_row=last_daily_row)
        dates = Reference(wb["Daily"], min_col=1, min_row=2, max_row=last_daily_row)
        chart.add_data(values, titles_from_data=True)
        chart.set_categories(dates)
        for series, colour in zip(chart.series, (NAVY, GREY)):
            series.graphicalProperties.line.solidFill = colour
            series.graphicalProperties.line.width = 22000
            series.smooth = False
        chart.x_axis.tickLblSkip = max(1, (last_daily_row - 1) // 6)
        _clean_chart(chart)
        chart.title.overlay = False
        ws.add_chart(chart, f"A{note + 2}")
    _widths(ws, [18, 12, 12, 12, 12, 12, 12])


def _risk_sheet(wb: Workbook, report: ReportData) -> None:
    ws = wb.create_sheet("Risk")
    _title(ws, "Risk", f"One year to {report.end:%b %d, %Y} (from {report.risk_start:%b %d, %Y})")
    _header(ws, 4, ["Statistic", "Portfolio", "Benchmark"])
    for i, rec in enumerate(report.risk.itertuples(index=False)):
        r = 5 + i
        fmt = RATIO if rec.statistic in RATIO_STATS else PCT
        _put(ws, r, 1, rec.statistic, font=BODY)
        _put(ws, r, 2, rec.portfolio, fmt)
        _put(ws, r, 3, rec.benchmark, fmt)
    note = 5 + len(report.risk) + 1
    notes = [
        "Annualized with 252 trading days. Risk-free rate: Bank of Canada 3-month T-bill yield.",
        "Tracking error and information ratio compare the portfolio with the benchmark.",
    ]
    for j, text in enumerate(notes):
        ws.cell(row=note + j, column=1, value=text).font = SUBTITLE
    _widths(ws, [22, 14, 14])


def _sectors_sheet(wb: Workbook, report: ReportData) -> None:
    ws = wb.create_sheet("Sectors")
    _title(ws, "Sector weights", f"Holdings as of {report.holdings_date:%b %d, %Y}")
    _header(ws, 4, ["Sector", "Weight", "Holdings"])
    first = 5
    for i, rec in enumerate(report.sectors.itertuples(index=False)):
        _put(ws, first + i, 1, rec.sector, font=BODY)
        _put(ws, first + i, 2, float(rec.weight), "0.00%")
        _put(ws, first + i, 3, int(rec.holdings), "0")
    last = first + len(report.sectors) - 1
    total = last + 1
    ws.cell(row=total, column=1, value="Total").font = BOLD
    ws.cell(row=total, column=2, value=f"=SUM(B{first}:B{last})").number_format = "0.00%"
    ws.cell(row=total, column=3, value=f"=SUM(C{first}:C{last})").number_format = "0"
    for col in (1, 2, 3):
        ws.cell(row=total, column=col).border = TOTAL_BORDER
        ws.cell(row=total, column=col).font = BOLD

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Weight by sector"
    chart.y_axis.number_format = "0%"
    chart.height, chart.width = 8, 14
    chart.add_data(Reference(ws, min_col=2, min_row=4, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    chart.x_axis.scaling.orientation = "maxMin"  # largest sector at the top
    chart.series[0].graphicalProperties.solidFill = NAVY
    chart.legend = None
    chart.gapWidth = 60
    _clean_chart(chart)
    chart.title.overlay = False
    ws.add_chart(chart, "E4")
    _widths(ws, [26, 12, 10])


def _top10_sheet(wb: Workbook, report: ReportData) -> None:
    ws = wb.create_sheet("Top 10")
    _title(ws, "Top 10 holdings",
           f"Weights as of {report.holdings_date:%b %d, %Y}; returns for {report.period_label}")
    _header(ws, 4, ["Ticker", "Name", "Sector", "Weight", "Period return", "Contribution"])
    first = 5
    for i, rec in enumerate(report.top10.itertuples(index=False)):
        r = first + i
        _put(ws, r, 1, rec.ticker, font=BODY)
        _put(ws, r, 2, rec.name, font=BODY)
        _put(ws, r, 3, rec.sector, font=BODY)
        _put(ws, r, 4, float(rec.weight), "0.00%")
        _put(ws, r, 5, rec.period_return, PCT)
        contribution = ws.cell(row=r, column=6, value=f'=IF(E{r}="","",D{r}*E{r})')
        contribution.font, contribution.number_format = BODY, PCT
    last = first + len(report.top10) - 1
    total = last + 1
    ws.cell(row=total, column=1, value="Top 10 total")
    ws.cell(row=total, column=4, value=f"=SUM(D{first}:D{last})").number_format = "0.00%"
    ws.cell(row=total, column=6, value=f"=SUM(F{first}:F{last})").number_format = PCT
    for col in range(1, 7):
        ws.cell(row=total, column=col).border = TOTAL_BORDER
        ws.cell(row=total, column=col).font = BOLD
    ws.cell(row=total + 2, column=1,
            value="Contribution = weight x period return, an estimate of each position's effect.").font = SUBTITLE
    _widths(ws, [11, 34, 24, 10, 14, 14])


def _summary_sheet(wb: Workbook, report: ReportData) -> None:
    """Front page. Every number links to the detail tabs (green), so it can never disagree."""
    ws = wb.create_sheet("Summary", 0)
    _title(ws, f"{report.strategy_name}: performance report", report.period_label)
    facts = [
        ("Strategy", report.strategy_name),
        ("Benchmark", report.benchmark_name),
        ("Inception", report.inception),
        ("Holdings as of", report.holdings_date),
        ("Prepared", date.today()),
    ]
    for i, (label, value) in enumerate(facts):
        ws.cell(row=4 + i, column=1, value=label).font = BOLD
        cell = _put(ws, 4 + i, 2, value, DATE_FMT if isinstance(value, date) else None, BODY)
        cell.alignment = Alignment(horizontal="left")

    _header(ws, 10, ["Key figures", "Portfolio", "Benchmark", "Difference"])
    perf_row = {p: 5 + i for i, p in enumerate(report.performance["period"])}
    risk_row = {s: 5 + i for i, s in enumerate(report.risk["statistic"])}
    lines = [
        ("Return for the period", "Performance", perf_row["Period"], PCT),
        ("Return year to date", "Performance", perf_row["Year to date"], PCT),
        ("Volatility (1 year)", "Risk", risk_row["Volatility"], PCT),
        ("Sharpe ratio (1 year)", "Risk", risk_row["Sharpe ratio"], RATIO),
        ("Max drawdown (1 year)", "Risk", risk_row["Max drawdown"], PCT),
    ]
    for i, (label, sheet, src, fmt) in enumerate(lines):
        r = 11 + i
        ws.cell(row=r, column=1, value=label).font = BODY
        p_col, b_col = ("D", "E") if sheet == "Performance" else ("B", "C")
        for col, ref in ((2, f"={sheet}!{p_col}{src}"), (3, f"={sheet}!{b_col}{src}")):
            cell = ws.cell(row=r, column=col, value=ref)
            cell.font, cell.number_format = LINK, fmt
        diff = ws.cell(row=r, column=4, value=f'=IF(OR(B{r}="",C{r}=""),"",B{r}-C{r})')
        diff.font, diff.number_format = BODY, fmt
    ws.cell(row=17, column=1, value="See the Notes tab for methods and data sources.").font = SUBTITLE
    _widths(ws, [26, 26, 14, 14])


def _notes_sheet(wb: Workbook, report: ReportData) -> None:
    ws = wb.create_sheet("Notes")
    _title(ws, "Notes", "Methods, data sources and how to read this workbook")
    lines = [
        ("Portfolio", ("Fictional portfolio of 43 TSX-listed stocks, built for demonstration. "
                      "Month-end weights apply from the next trading day.")),
        ("Prices", ("Daily closing prices from Yahoo Finance (via the yfinance library), "
                   "split-adjusted, dividends excluded: these are price returns.")),
        ("Benchmark", f"{report.benchmark_name} ({report.benchmark_ticker}), price index."),
        ("Risk-free rate", "Bank of Canada Valet API, 3-month Government of Canada T-bill yield."),
        ("Returns", "Daily returns are compounded. Periods longer than one year are annualized."),
        ("Risk", "Volatility, Sharpe ratio, tracking error and information ratio use 252 trading days."),
        ("Font colours", ("Blue = data loaded from the database. Black = formula. "
                         "Green = link to another tab.")),
        ("Generated", f"{date.today():%Y-%m-%d} by report_agent (github.com/Frobles26/client-report-agent)."),
    ]
    for i, (label, text) in enumerate(lines):
        ws.cell(row=4 + i, column=1, value=label).font = BOLD
        cell = ws.cell(row=4 + i, column=2, value=text)
        cell.font = BODY
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=4 + i, column=1).alignment = Alignment(vertical="top")
    _widths(ws, [16, 90])


# ---------- public ----------

def build_excel(report: ReportData, path: Path | None = None) -> Path:
    """Write the workbook and return its path."""
    path = path or OUTPUT_DIR / f"{report.strategy_id}_{report.start}_{report.end}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    wb.remove(wb.active)
    last_daily_row = _daily_sheet(wb, report)          # first: other tabs chart from it
    _performance_sheet(wb, report, last_daily_row)
    _risk_sheet(wb, report)
    _sectors_sheet(wb, report)
    _top10_sheet(wb, report)
    _summary_sheet(wb, report)
    _notes_sheet(wb, report)
    wb.move_sheet("Daily", offset=len(wb.sheetnames))  # data tab goes last but one
    wb.move_sheet("Notes", offset=len(wb.sheetnames))
    wb.active = 0
    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"        # print each tab on one page width
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    wb.calculation.fullCalcOnLoad = True               # Excel recalculates every formula on open
    wb.save(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Excel performance workbook.")
    parser.add_argument("--strategy", default="CAN_EQ")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    try:
        report = build_report_data(args.strategy, args.start, args.end)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Saved {build_excel(report)}")


if __name__ == "__main__":
    main()
