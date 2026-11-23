"""Agent tests. A fake Claude client plays back scripted replies, so no API key, no cost and
no database are needed. The real tools run (they build real Excel and PowerPoint files)."""

import json
from types import SimpleNamespace

import pytest
from pptx import Presentation

from report_agent.agent.loop import run_agent
from report_agent.agent.tools import ReportSession, ToolError, report_facts, unknown_figures

GOOD_COMMENTARY = (
    "The Canadian Equity portfolio returned +1.20% for the quarter, ahead of the "
    "S&P/TSX Composite Index at +1.00%, an active return of +0.20%.\n\n"
    "Royal Bank was the largest contributor at +1.50%, helped by a +5.00% return. "
    "Enbridge took away 0.40% after falling 2.00%. Financials remain the largest sector "
    "at 60.0% of the portfolio.\n\n"
    "Over the past year the portfolio's volatility was 12.00% against 13.00% for the "
    "benchmark, and its beta of 0.95 means it moved slightly less than the market."
)


# ---------- a fake Claude ----------

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args=None, id_=None):
    return SimpleNamespace(type="tool_use", id=id_ or f"tool_{name}", name=name,
                           input=args or {})


def reply(*blocks):
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    """Stands in for anthropic.Anthropic: messages.create returns the next scripted reply."""

    def __init__(self, replies):
        self._replies = iter(replies)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        return next(self._replies)


@pytest.fixture
def session(report, tmp_path):
    status = {"today": "2026-11-23", "strategies": [
        {"strategy_id": "CAN_EQ", "latest_return_date": "2026-11-20"}]}
    return ReportSession(report_loader=lambda *_: report, status_loader=lambda: status,
                         data_checker=lambda *_: [], output_dir=tmp_path)


# ---------- the number guard ----------

def test_facts_are_formatted_like_the_deck(report):
    facts = report_facts(report)
    assert facts["performance"][0] == {"period": "Period", "from": "Jul 01, 2026",
                                       "portfolio": "+1.20%", "benchmark": "+1.00%",
                                       "active": "+0.20%"}
    assert facts["performance"][2]["portfolio"] == "–"          # missing value
    assert facts["top10_total_contribution"] == "+1.10%"


def test_commentary_figures_must_come_from_the_report(report):
    facts = report_facts(report)
    assert unknown_figures(GOOD_COMMENTARY, facts) == []
    assert unknown_figures("It returned 1.2% (rounded) and 1%.", facts) == []
    assert unknown_figures("It returned +1.35% with a beta of 0.99.", facts) == ["0.99", "1.35%"]


def test_write_commentary_rejects_made_up_numbers(session):
    session.get_report_numbers("CAN_EQ", "2026-07-01", "2026-09-30")
    with pytest.raises(ToolError, match="2.50%"):
        session.write_commentary(GOOD_COMMENTARY.replace("+1.20%", "+2.50%"))
    with pytest.raises(ToolError, match="words"):
        session.write_commentary("Returned +1.20%.")


def test_tools_must_run_in_order(session):
    with pytest.raises(ToolError, match="get_report_numbers first"):
        session.build_excel()
    session.get_report_numbers("CAN_EQ", "2026-07-01", "2026-09-30")
    with pytest.raises(ToolError, match="write_commentary first"):
        session.build_deck()


def test_bad_dates_are_a_tool_error(session):
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        session.get_report_numbers("CAN_EQ", "Q3 2026", "2026-09-30")


def test_period_past_the_data_is_refused(session):
    with pytest.raises(ToolError, match="data ends 2026-09-30"):
        session.get_report_numbers("CAN_EQ", "2026-10-01", "2026-12-31")
    assert session.report is None


# ---------- the loop ----------

def test_full_run_builds_both_files_and_recovers_from_an_error(session, tmp_path):
    numbers = {"strategy_id": "CAN_EQ", "start": "2026-07-01", "end": "2026-09-30"}
    client = FakeClient([
        reply(text_block("Checking the data."), tool_block("get_data_status")),
        reply(tool_block("get_report_numbers", numbers)),
        reply(tool_block("build_excel")),
        reply(tool_block("write_commentary",
                         {"text": GOOD_COMMENTARY.replace("+1.20%", "+1.25%")}, "c1")),
        reply(tool_block("write_commentary", {"text": GOOD_COMMENTARY}, "c2")),
        reply(tool_block("build_deck")),
        reply(tool_block("run_quality_checks")),
        reply(text_block("Built the Q3 2026 Excel and deck. The portfolio returned +1.20%.")),
    ])
    result = run_agent("Build the Q3 2026 report for CAN_EQ", client, session,
                       log_path=tmp_path / "run_log.json")

    assert result.status == "completed"
    assert set(result.files) == {"excel", "deck"}
    # the made-up +1.25% went back to the model as an error, and it corrected itself
    errors = [c for s in result.steps for c in s["tool_calls"] if c["is_error"]]
    assert len(errors) == 1 and "1.25%" in errors[0]["output"]
    sent_back = client.requests[4][-1]["content"][0]
    assert sent_back["tool_use_id"] == "c1" and sent_back["is_error"] is True

    deck = Presentation(result.files["deck"])
    texts = [sh.text_frame.text for sh in deck.slides[6].shapes if sh.has_text_frame]
    assert any("Royal Bank was the largest contributor" in t for t in texts)

    log = json.loads(result.log_path.read_text())
    assert log["status"] == "completed" and log["steps_used"] == 8
    assert log["usage"] == {"input_tokens": 800, "output_tokens": 160}
    assert result.checks_overall == "pass" and log["checks_overall"] == "pass"
    assert log["checks"] == [{"name": "files_match_report", "status": "pass",
                              "detail": "Excel and PowerPoint match the analytics"}]
    assert log["commentary"] == GOOD_COMMENTARY


def test_stops_at_the_step_limit(session, tmp_path):
    client = FakeClient([reply(tool_block("get_data_status"))] * 5)
    result = run_agent("loop forever", client, session, max_steps=3,
                       log_path=tmp_path / "run_log.json")
    assert result.status == "step_limit"
    assert len(client.requests) == 3


def test_unknown_tool_is_reported_not_raised(session, tmp_path):
    client = FakeClient([reply(tool_block("delete_database")), reply(text_block("Sorry."))])
    result = run_agent("x", client, session, log_path=tmp_path / "run_log.json")
    assert result.status == "completed"
    assert result.steps[0]["tool_calls"][0]["is_error"] is True


def test_thinking_is_sent_back_unchanged_and_logged(session, tmp_path):
    thinking = SimpleNamespace(type="thinking", thinking="Q3 is Jul 1 to Sep 30.",
                               signature="sig123")
    client = FakeClient([reply(thinking, tool_block("get_data_status")),
                         reply(text_block("Done."))])
    result = run_agent("x", client, session, log_path=tmp_path / "run_log.json")
    assert result.status == "completed"
    assert client.requests[1][1]["content"][0] == {
        "type": "thinking", "thinking": "Q3 is Jul 1 to Sep 30.", "signature": "sig123"}
    assert result.steps[0]["thinking"] == "Q3 is Jul 1 to Sep 30."


def test_api_error_stops_the_run_and_still_writes_the_log(session, tmp_path):
    class BrokenClient:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **_):
            raise ConnectionError("network down")

    result = run_agent("x", BrokenClient(), session, log_path=tmp_path / "run_log.json")
    assert result.status == "error" and "network down" in result.final_text
    assert json.loads(result.log_path.read_text())["status"] == "error"


def test_checks_need_a_loaded_report(session):
    with pytest.raises(ToolError, match="get_report_numbers first"):
        session.run_quality_checks()
