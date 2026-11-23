"""PowerPoint output tests. They use the made-up report in conftest.py, so no database is needed."""

import pytest
from pptx import Presentation

from report_agent.outputs.deck import (
    PLACEHOLDER,
    build_deck,
    pct,
    performance_title,
    risk_title,
    top10_title,
)


@pytest.fixture
def deck(report, tmp_path):
    return Presentation(build_deck(report, tmp_path / "report.pptx"))


def _slide_text(slide) -> str:
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            parts.extend(cell.text for row in shape.table.rows for cell in row.cells)
    return "\n".join(parts)


def test_pct_formats_and_handles_missing():
    assert pct(0.0123) == "1.23%"
    assert pct(0.0123, signed=True) == "+1.23%"
    assert pct(-0.05, signed=True, digits=1) == "-5.0%"
    assert pct(float("nan")) == "–"
    assert pct(None) == "–"


def test_titles_state_the_main_point(report):
    assert performance_title(report) == "Returned +1.20%, 0.20% ahead of the benchmark"
    assert risk_title(report) == "Less volatile than the benchmark: 12.0% vs 13.0%"
    assert top10_title(report) == "The top 10 holdings added 1.10% to the return"


def test_eight_slides_widescreen(deck):
    assert len(deck.slides) == 8
    assert round(deck.slide_width / deck.slide_height, 2) == 1.78   # 16:9


def test_charts_are_native(deck):
    charts = [s for slide in deck.slides for s in slide.shapes if s.has_chart]
    assert len(charts) == 3                    # growth line, returns by period, sectors


def test_growth_chart_ends_at_compounded_return(deck, report):
    chart = next(s.chart for s in deck.slides[1].shapes if s.has_chart)
    last = list(chart.plots[0].series[0].values)[-1]
    expected = 100 * (1 + report.daily["portfolio"]).prod()
    assert last == pytest.approx(expected, abs=0.01)


def test_top10_table_rows_and_total(deck):
    table = next(s.table for s in deck.slides[5].shapes if s.has_table)
    assert len(table.rows) == 1 + 2 + 1          # header, 2 holdings, total
    assert table.cell(3, 1).text == "Top 10 total"
    assert table.cell(3, 6).text == "+1.10%"


def test_commentary_placeholder_then_text(report, tmp_path):
    empty = Presentation(build_deck(report, tmp_path / "a.pptx"))
    assert PLACEHOLDER in _slide_text(empty.slides[6])
    filled = Presentation(build_deck(report, tmp_path / "b.pptx",
                                     commentary="Banks led.\nEnergy lagged."))
    text = _slide_text(filled.slides[6])
    assert "Banks led." in text and "Energy lagged." in text
    assert PLACEHOLDER not in text


def test_missing_numbers_show_a_dash_not_nan(deck):
    for slide in deck.slides:
        words = _slide_text(slide).lower().replace("%", " ").split()
        assert "nan" not in words and "nan%" not in words


def test_every_slide_has_speaker_notes(deck):
    for slide in deck.slides:
        assert slide.notes_slide.notes_text_frame.text.strip()
