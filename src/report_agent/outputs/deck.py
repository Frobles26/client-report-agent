"""Build the client PowerPoint deck for a report.

Slides: cover, performance, returns by period, risk, sectors, top 10 holdings,
commentary, notes. Charts are native PowerPoint charts (not pictures), so a reviewer can
click one and see or edit its numbers. Slide titles are written from the numbers, so each
slide states its main point ("The portfolio returned +1.2%, 0.3% ahead of the benchmark").

The commentary slide holds a placeholder until the agent (Day 6) writes the text:
build_deck(report, commentary="...") puts that text in.

Run: python -m report_agent.outputs.deck --start 2026-07-01 --end 2026-09-30
"""

import argparse
import math
from datetime import date
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import (
    XL_CHART_TYPE,
    XL_LEGEND_POSITION,
    XL_MARKER_STYLE,
    XL_TICK_LABEL_POSITION,
)
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from report_agent.analytics.report import ReportData, build_report_data

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"

FONT = "Arial"
NAVY = RGBColor(0x1F, 0x3A, 0x5F)      # portfolio, titles, cover background
GREY = RGBColor(0x8C, 0x96, 0xA3)      # benchmark
TEXT = RGBColor(0x26, 0x32, 0x38)
MUTED = RGBColor(0x5F, 0x6B, 0x7A)
TINT = RGBColor(0xEE, 0xF2, 0xF7)      # card background
LINE = RGBColor(0xD5, 0xDB, 0xE3)
ICE = RGBColor(0xCA, 0xDC, 0xFC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
RED = RGBColor(0xC6, 0x28, 0x28)

SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.6
CONTENT_TOP = 1.75                     # where content starts under the title block
PLACEHOLDER = ("Commentary will be drafted by the report agent from the numbers in this "
               "deck, then reviewed by an analyst before it is sent.")


# ---------- number formatting ----------

def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct(x, signed: bool = False, digits: int = 2) -> str:
    """0.0123 -> '1.23%' (or '+1.23%' when signed). Missing -> an en dash."""
    if _missing(x):
        return "–"
    return f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"


def ratio(x) -> str:
    return "–" if _missing(x) else f"{x:.2f}"


# ---------- slide titles: the main point of each slide, written from the numbers ----------

def _row(df: pd.DataFrame, key_col: str, key: str) -> pd.Series | None:
    match = df[df[key_col] == key]
    return None if match.empty else match.iloc[0]


def performance_title(report: ReportData) -> str:
    row = _row(report.performance, "period", "Period")
    if row is None or _missing(row["portfolio"]) or _missing(row["active"]):
        return "Performance for the period"
    side = "ahead of" if row["active"] >= 0 else "behind"
    return f"Returned {pct(row['portfolio'], True)}, {pct(abs(row['active']))} {side} the benchmark"


def risk_title(report: ReportData) -> str:
    row = _row(report.risk, "statistic", "Volatility")
    if row is None or _missing(row["portfolio"]) or _missing(row["benchmark"]):
        return "Risk over the last year"
    more = "Less" if row["portfolio"] < row["benchmark"] else "More"
    return (f"{more} volatile than the benchmark: "
            f"{pct(row['portfolio'], digits=1)} vs {pct(row['benchmark'], digits=1)}")


def sector_title(report: ReportData) -> str:
    if report.sectors.empty:
        return "Sector weights"
    top = report.sectors.sort_values("weight", ascending=False).iloc[0]
    return f"{top['sector']} is the largest sector at {pct(top['weight'], digits=1)}"


def top10_title(report: ReportData) -> str:
    total = report.top10["contribution"].sum(min_count=1)
    if _missing(total):
        return "Top 10 holdings"
    verb = "added" if total >= 0 else "took"
    return f"The top 10 holdings {verb} {pct(abs(total))} {'to' if total >= 0 else 'from'} the return"


# ---------- drawing helpers ----------

def _text(slide, x, y, w, h, text, size=14, bold=False, color=TEXT, align=PP_ALIGN.LEFT,
          italic=False, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        f = run.font
        f.name, f.size, f.bold, f.italic = FONT, Pt(size), bold, italic
        f.color.rgb = color
    return box


def _card(slide, x, y, w, h, fill=TINT):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _stat(slide, x, y, w, h, label, value, color=NAVY, note=""):
    """A big number with a small label: the deck's repeated visual element."""
    _card(slide, x, y, w, h)
    _text(slide, x + 0.3, y + 0.22, w - 0.6, 0.3, label.upper(), 11, True, MUTED)
    _text(slide, x + 0.3, y + 0.52, w - 0.6, 0.7, value, 36, True, color)
    if note:
        _text(slide, x + 0.3, y + h - 0.45, w - 0.6, 0.3, note, 11, color=MUTED)


def _signed_color(x) -> RGBColor:
    if _missing(x):
        return MUTED
    return GREEN if x >= 0 else RED


def _content_slide(prs, report: ReportData, title: str, subtitle: str, notes: str):
    slide = prs.slides.add_slide(prs.slide_layouts[6])            # blank layout
    _text(slide, MARGIN, 0.5, SLIDE_W - 2 * MARGIN, 0.6, title, 28, True, NAVY)
    _text(slide, MARGIN, 1.12, SLIDE_W - 2 * MARGIN, 0.35, subtitle, 14, color=MUTED)
    footer = f"{report.strategy_name}  |  {report.period_label}"
    _text(slide, MARGIN, SLIDE_H - 0.5, 8, 0.25, footer, 10, color=MUTED)
    _text(slide, SLIDE_W - MARGIN - 1, SLIDE_H - 0.5, 1, 0.25, str(len(prs.slides)), 10,
          color=MUTED, align=PP_ALIGN.RIGHT)
    slide.notes_slide.notes_text_frame.text = notes
    return slide


def _style_chart(chart, legend: bool) -> None:
    chart.font.name = FONT
    chart.font.size = Pt(11)
    chart.font.color.rgb = MUTED
    chart.has_title = False
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(12)


def _quiet_axes(chart, value_format: str, gridlines: bool = True) -> None:
    va = chart.value_axis
    va.has_major_gridlines = gridlines
    if gridlines:
        va.major_gridlines.format.line.color.rgb = LINE
    va.format.line.fill.background()
    va.tick_labels.number_format = value_format
    va.tick_labels.number_format_is_linked = False
    ca = chart.category_axis
    ca.format.line.color.rgb = LINE
    ca.has_major_gridlines = False


def _label_every(category_axis, n: int) -> None:
    """Show every n-th category label (python-pptx has no setting for this, so edit the XML)."""
    ax = category_axis._element
    for tag in ("c:tickLblSkip", "c:tickMarkSkip"):
        el = ax.find(qn(tag))
        if el is None:
            el = ax.makeelement(qn(tag), {})
            no_multi = ax.find(qn("c:noMultiLvlLbl"))
            if no_multi is not None:
                no_multi.addprevious(el)
            else:
                ax.append(el)
        el.set("val", str(n))


def _table(slide, x, y, w, rows: list[list[str]], col_widths: list[float], row_h=0.42,
           colors: dict[tuple[int, int], RGBColor] | None = None, bold_last=False):
    """First row is the header. Text columns left-aligned, number columns right-aligned."""
    colors = colors or {}
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y),
                                   Inches(w), Inches(row_h * len(rows)))
    table = shape.table
    for j, cw in enumerate(col_widths):
        table.columns[j].width = Inches(cw)
    for i, values in enumerate(rows):
        table.rows[i].height = Inches(row_h)
        for j, value in enumerate(values):
            cell = table.cell(i, j)
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if i == 0 else (TINT if i % 2 == 0 else WHITE)
            p = cell.text_frame.paragraphs[0]
            numeric = j > 0 and _is_number_text(value)
            p.alignment = PP_ALIGN.RIGHT if numeric or (i == 0 and j > 0 and
                                                       _column_numeric(rows, j)) else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = value
            f = run.font
            f.name, f.size = FONT, Pt(13)
            f.bold = i == 0 or (bold_last and i == len(rows) - 1)
            f.color.rgb = WHITE if i == 0 else colors.get((i, j), TEXT)
    return table


def _is_number_text(value: str) -> bool:
    return value == "–" or value.lstrip("+-").replace(".", "", 1).rstrip("%").isdigit()


def _column_numeric(rows: list[list[str]], j: int) -> bool:
    return all(_is_number_text(r[j]) for r in rows[1:])


# ---------- slides ----------

def _cover(prs, report: ReportData) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = NAVY
    _text(slide, 0.9, 2.2, 11, 0.9, report.strategy_name, 44, True, WHITE)
    _text(slide, 0.9, 3.1, 11, 0.6, "Performance report", 24, color=ICE)
    _text(slide, 0.9, 4.2, 11, 0.4, report.period_label, 18, color=WHITE)
    _text(slide, 0.9, 4.65, 11, 0.4, f"Benchmark: {report.benchmark_name}", 14, color=ICE)
    _text(slide, 0.9, SLIDE_H - 0.9, 11, 0.3,
          f"Prepared {date.today():%b %d, %Y}  |  For discussion purposes only", 11, color=ICE)
    slide.notes_slide.notes_text_frame.text = (
        f"Report for {report.strategy_name} ({report.strategy_id}) from {report.start} "
        f"to {report.end}, compared with {report.benchmark_name}.")


def _performance(prs, report: ReportData) -> None:
    row = _row(report.performance, "period", "Period")
    slide = _content_slide(prs, report, performance_title(report),
                           f"Return for {report.period_label}, and growth of 100 invested "
                           "at the start", "Portfolio and benchmark return for the period. "
                           "Active return is the difference. Returns exclude dividends.")
    stats = [("Portfolio", row["portfolio"] if row is not None else None, NAVY),
             ("Benchmark", row["benchmark"] if row is not None else None, GREY),
             ("Active", row["active"] if row is not None else None, None)]
    y = CONTENT_TOP
    for label, value, color in stats:
        _stat(slide, MARGIN, y, 3.4, 1.5, label, pct(value, True),
              color or _signed_color(value))
        y += 1.5 + 0.3

    growth = 100 * (1 + report.daily[["portfolio", "benchmark"]]).cumprod()
    data = CategoryChartData()
    data.categories = [f"{d:%b} {d.day}" for d in growth.index]   # text labels, not a date axis
    data.add_series("Portfolio", growth["portfolio"].round(2).tolist())
    data.add_series(report.benchmark_name, growth["benchmark"].round(2).tolist())
    x = MARGIN + 3.4 + 0.5
    frame = slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(x), Inches(CONTENT_TOP),
                                   Inches(SLIDE_W - MARGIN - x), Inches(5.1), data)
    chart = frame.chart
    _style_chart(chart, legend=True)
    _quiet_axes(chart, "0")
    low, high = growth.min().min(), growth.max().max()
    pad = max((high - low) * 0.1, 0.5)
    chart.value_axis.minimum_scale = math.floor(low - pad)          # zoom in on the data
    chart.value_axis.maximum_scale = math.ceil(high + pad)
    ca = chart.category_axis
    ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    _label_every(ca, max(1, len(growth) // 6))         # about 6 date labels
    for series, color in zip(chart.plots[0].series, [NAVY, GREY]):
        series.smooth = False
        series.format.line.color.rgb = color
        series.format.line.width = Pt(2.25)
        series.marker.style = XL_MARKER_STYLE.NONE


def _periods(prs, report: ReportData) -> None:
    perf = report.performance
    slide = _content_slide(prs, report, "Returns over longer periods",
                           "Periods longer than one year are annualized",
                           "Returns from each start date to the report end date. "
                           "Since inception is annualized when the span is over a year.")
    rows = [["Period", "From", "Portfolio", "Benchmark", "Active"]]
    colors = {}
    for i, r in enumerate(perf.itertuples(), start=1):
        label = r.period + (" (ann.)" if r.annualized else "")
        rows.append([label, f"{pd.Timestamp(r.start):%b %d, %Y}", pct(r.portfolio, True),
                     pct(r.benchmark, True), pct(r.active, True)])
        colors[(i, 4)] = _signed_color(r.active)
    _table(slide, MARGIN, CONTENT_TOP, 6.6, rows, [1.8, 1.35, 1.15, 1.3, 1.0], row_h=0.5,
           colors=colors)

    data = CategoryChartData(number_format="0.0%")
    data.categories = [p + (" (ann.)" if a else "") for p, a in zip(perf["period"], perf["annualized"])]
    data.add_series("Portfolio", [None if _missing(v) else float(v) for v in perf["portfolio"]])
    data.add_series(report.benchmark_name,
                    [None if _missing(v) else float(v) for v in perf["benchmark"]])
    x = MARGIN + 6.6 + 0.5
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x),
                                   Inches(CONTENT_TOP), Inches(SLIDE_W - MARGIN - x),
                                   Inches(5.1), data).chart
    _style_chart(chart, legend=True)
    _quiet_axes(chart, "0%")
    chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    plot = chart.plots[0]
    plot.gap_width = 60
    plot.overlap = -10
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = "0.0%"
    labels.number_format_is_linked = False
    labels.font.size = Pt(11)
    for series, color in zip(plot.series, [NAVY, GREY]):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = color
        series.invert_if_negative = False


def _risk(prs, report: ReportData) -> None:
    slide = _content_slide(prs, report, risk_title(report),
                           f"Measured over the year from {report.risk_start:%b %d, %Y} to "
                           f"{report.end:%b %d, %Y}, annualized with 252 trading days",
                           "Risk uses a full year of daily returns because one quarter has "
                           "too few days for stable statistics.")
    rows = [["Statistic", "Portfolio", "Benchmark"]]
    for r in report.risk.itertuples():
        fmt = ratio if r.statistic in {"Sharpe ratio", "Beta", "Information ratio"} else pct
        rows.append([r.statistic, fmt(r.portfolio), fmt(r.benchmark)])
    _table(slide, MARGIN, CONTENT_TOP, 6.6, rows, [3.0, 1.8, 1.8], row_h=0.55)

    risk = report.risk.set_index("statistic")["portfolio"]
    explain = [
        ("Beta", ratio(risk.get("Beta")), "Moves about this much for each 1.00 move in the benchmark"),
        ("Tracking error", pct(risk.get("Tracking error"), digits=1),
         "Yearly spread of returns around the benchmark"),
    ]
    x, w = MARGIN + 6.6 + 0.5, SLIDE_W - 2 * MARGIN - 6.6 - 0.5
    y = CONTENT_TOP
    for label, value, note in explain:
        _stat(slide, x, y, w, 1.75, label, value, note=note)
        y += 1.75 + 0.3


def _sectors(prs, report: ReportData) -> None:
    sectors = report.sectors.sort_values("weight")            # bar charts draw bottom-up
    slide = _content_slide(prs, report, sector_title(report),
                           f"Portfolio weights by sector, from holdings on "
                           f"{report.holdings_date:%b %d, %Y}",
                           "Weights are the month-end holdings in effect at the start of the "
                           "period.")
    data = CategoryChartData(number_format="0.0%")
    data.categories = sectors["sector"].tolist()
    data.add_series("Weight", sectors["weight"].astype(float).tolist())
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(MARGIN),
                                   Inches(CONTENT_TOP), Inches(8.0), Inches(5.1), data).chart
    _style_chart(chart, legend=False)
    _quiet_axes(chart, "0%", gridlines=False)
    chart.value_axis.visible = False
    plot = chart.plots[0]
    plot.gap_width = 45
    plot.has_data_labels = True
    plot.data_labels.number_format = "0.0%"
    plot.data_labels.number_format_is_linked = False
    plot.data_labels.font.size = Pt(12)
    plot.data_labels.font.color.rgb = TEXT
    series = plot.series[0]
    series.format.fill.solid()
    series.format.fill.fore_color.rgb = NAVY

    x, w = MARGIN + 8.0 + 0.5, SLIDE_W - 2 * MARGIN - 8.0 - 0.5
    _stat(slide, x, CONTENT_TOP, w, 1.5, "Holdings", str(int(report.sectors["holdings"].sum())))
    _stat(slide, x, CONTENT_TOP + 1.8, w, 1.5, "Sectors", str(len(report.sectors)))


def _top10(prs, report: ReportData) -> None:
    top = report.top10
    slide = _content_slide(prs, report, top10_title(report),
                           "Largest holdings at the start of the period. "
                           "Contribution = weight × period return",
                           "Contribution is an approximation: start weight times the stock's "
                           "return for the period.")
    rows = [["#", "Company", "Ticker", "Sector", "Weight", "Return", "Contribution"]]
    colors = {}
    for i, r in enumerate(top.itertuples(), start=1):
        rows.append([str(i), r.name, r.ticker, r.sector, pct(r.weight),
                     pct(r.period_return, True), pct(r.contribution, True)])
        colors[(i, 5)] = _signed_color(r.period_return)
        colors[(i, 6)] = _signed_color(r.contribution)
    rows.append(["", "Top 10 total", "", "", pct(top["weight"].sum()), "",
                 pct(top["contribution"].sum(min_count=1), True)])
    _table(slide, MARGIN, CONTENT_TOP - 0.05, SLIDE_W - 2 * MARGIN, rows,
           [0.5, 3.6, 1.5, 2.4, 1.3, 1.3, 1.533], row_h=0.41, colors=colors, bold_last=True)


def _commentary(prs, report: ReportData, commentary: str | None) -> None:
    slide = _content_slide(prs, report, "Commentary",
                           "What drove the period, in plain language",
                           "Draft commentary. Check every figure against the tables before "
                           "sending.")
    w = 7.4
    _card(slide, MARGIN, CONTENT_TOP, w, 5.1, fill=WHITE).line.color.rgb = LINE
    if commentary:
        paragraphs = [p.strip() for p in commentary.split("\n") if p.strip()]
        box = _text(slide, MARGIN + 0.35, CONTENT_TOP + 0.3, w - 0.7, 4.5, paragraphs, 15)
        for p in box.text_frame.paragraphs:
            p.space_after = Pt(10)
    else:
        _text(slide, MARGIN + 0.35, CONTENT_TOP + 0.3, w - 0.7, 1.2, PLACEHOLDER, 15,
              italic=True, color=MUTED)

    perf = _row(report.performance, "period", "Period")
    best = report.top10.sort_values("contribution", ascending=False).iloc[0] \
        if not report.top10.empty else None
    facts = [
        ("Period return", pct(perf["portfolio"], True) if perf is not None else "–"),
        ("Active return", pct(perf["active"], True) if perf is not None else "–"),
        ("Top contributor", "–" if best is None else
         f"{best['name']} ({pct(best['contribution'], True)})"),
        ("Largest sector", "–" if report.sectors.empty else
         report.sectors.sort_values("weight").iloc[-1]["sector"]),
    ]
    x, cw = MARGIN + w + 0.5, SLIDE_W - 2 * MARGIN - w - 0.5
    _card(slide, x, CONTENT_TOP, cw, 5.1)
    _text(slide, x + 0.3, CONTENT_TOP + 0.25, cw - 0.6, 0.3, "KEY FIGURES", 11, True, MUTED)
    y = CONTENT_TOP + 0.75
    for label, value in facts:
        _text(slide, x + 0.3, y, cw - 0.6, 0.3, label, 12, color=MUTED)
        _text(slide, x + 0.3, y + 0.3, cw - 0.6, 0.65, value, 17, True, NAVY)
        y += 1.05


def _notes(prs, report: ReportData) -> None:
    slide = _content_slide(prs, report, "Notes and methods",
                           "Data sources and how the numbers are calculated",
                           "Methods match the Excel workbook's Notes tab.")
    lines = [
        ("Portfolio", ("Fictional portfolio of 43 TSX-listed stocks, built for demonstration. "
                      "Month-end weights apply from the next trading day.")),
        ("Prices", ("Daily closes from Yahoo Finance (yfinance), split-adjusted. Dividends "
                   "are excluded, so these are price returns.")),
        ("Benchmark", f"{report.benchmark_name} ({report.benchmark_ticker}), price index."),
        ("Risk-free rate", "Bank of Canada Valet API, 3-month Government of Canada T-bill."),
        ("Returns", "Daily returns are compounded. Periods longer than one year are annualized."),
        ("Risk", ("Volatility, Sharpe ratio, tracking error and information ratio use 252 "
                 "trading days a year.")),
        ("Generated", (f"{date.today():%Y-%m-%d} by report_agent "
                      "(github.com/Frobles26/client-report-agent).")),
    ]
    _card(slide, MARGIN, CONTENT_TOP, SLIDE_W - 2 * MARGIN, 5.1)
    y = CONTENT_TOP + 0.35
    for label, text in lines:
        _text(slide, MARGIN + 0.35, y, 2.4, 0.6, label, 14, True, NAVY)
        _text(slide, MARGIN + 2.8, y, SLIDE_W - 2 * MARGIN - 3.15, 0.6, text, 14)
        y += 0.64


# ---------- public ----------

def build_deck(report: ReportData, path: Path | None = None,
               commentary: str | None = None) -> Path:
    """Write the deck and return its path."""
    path = path or OUTPUT_DIR / f"{report.strategy_id}_{report.start}_{report.end}.pptx"
    path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)   # 16:9
    prs.core_properties.title = f"{report.strategy_name}: {report.period_label}"
    prs.core_properties.author = "report_agent"
    _cover(prs, report)
    _performance(prs, report)
    _periods(prs, report)
    _risk(prs, report)
    _sectors(prs, report)
    _top10(prs, report)
    _commentary(prs, report, commentary)
    _notes(prs, report)
    prs.save(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PowerPoint performance deck.")
    parser.add_argument("--strategy", default="CAN_EQ")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    try:
        report = build_report_data(args.strategy, args.start, args.end)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Saved {build_deck(report)}")


if __name__ == "__main__":
    main()
