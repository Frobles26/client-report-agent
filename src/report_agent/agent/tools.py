"""The tools the agent can call, and the code behind each one.

The model never calculates a number. It asks for the numbers (get_report_numbers), gets
them already formatted ("+1.23%"), and may only repeat those exact figures in its
commentary: write_commentary rejects any percentage or ratio that is not in the report.

Each tool returns a JSON-ready dict. A tool that fails raises ToolError; the loop sends
the message back to the model as an error so it can correct itself.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from report_agent.analytics.report import ReportData, build_report_data
from report_agent.outputs.deck import build_deck, pct, ratio
from report_agent.outputs.excel import build_excel

RATIO_STATS = {"Sharpe ratio", "Beta", "Information ratio"}


class ToolError(Exception):
    """A problem the model can fix (bad dates, a step done out of order, a wrong number)."""


# ---------- tool definitions sent to Claude (name, what it does, input schema) ----------

TOOLS = [
    {
        "name": "get_data_status",
        "description": ("List the strategies in the database with their benchmark, inception "
                        "date and the latest date that has returns. Call this first to choose "
                        "valid report dates."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_report_numbers",
        "description": ("Calculate performance, risk, sector weights and top 10 holdings for one "
                        "strategy and period, and load them for the other tools. Returns every "
                        "figure already formatted. Use these figures exactly; never calculate."),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string", "description": "For example CAN_EQ"},
                "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
            },
            "required": ["strategy_id", "start", "end"],
        },
    },
    {
        "name": "build_excel",
        "description": "Build the Excel workbook for the loaded report. Returns the file path.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_commentary",
        "description": ("Save the client commentary for the deck. Every percentage and ratio in "
                        "the text must appear in get_report_numbers; otherwise the tool returns "
                        "an error naming the figures to fix. Separate paragraphs with a blank "
                        "line."),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "build_deck",
        "description": ("Build the PowerPoint deck for the loaded report, with the saved "
                        "commentary. Call write_commentary first. Returns the file path."),
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ---------- formatting the numbers the model sees ----------

def report_facts(report: ReportData) -> dict:
    """Every figure the model may use, formatted the same way as in the Excel and deck."""
    def fmt_risk(stat, x):
        return ratio(x) if stat in RATIO_STATS else pct(x)

    return {
        "strategy": f"{report.strategy_name} ({report.strategy_id})",
        "benchmark": report.benchmark_name,
        "period": report.period_label,
        "risk_window": f"{report.risk_start:%b %d, %Y} to {report.end:%b %d, %Y}",
        "holdings_date": f"{report.holdings_date:%b %d, %Y}",
        "performance": [
            {"period": r.period + (" (annualized)" if r.annualized else ""),
             "from": f"{pd.Timestamp(r.start):%b %d, %Y}",
             "portfolio": pct(r.portfolio, True), "benchmark": pct(r.benchmark, True),
             "active": pct(r.active, True)}
            for r in report.performance.itertuples()
        ],
        "risk": [
            {"statistic": r.statistic, "portfolio": fmt_risk(r.statistic, r.portfolio),
             "benchmark": fmt_risk(r.statistic, r.benchmark)}
            for r in report.risk.itertuples()
        ],
        "sectors": [
            {"sector": r.sector, "weight": pct(r.weight, digits=1), "holdings": int(r.holdings)}
            for r in report.sectors.sort_values("weight", ascending=False).itertuples()
        ],
        "top10": [
            {"name": r.name, "ticker": r.ticker, "sector": r.sector, "weight": pct(r.weight),
             "period_return": pct(r.period_return, True),
             "contribution": pct(r.contribution, True)}
            for r in report.top10.itertuples()
        ],
        "top10_total_contribution": pct(report.top10["contribution"].sum(min_count=1), True),
        "note": "Missing values are shown as '–'. Returns exclude dividends.",
    }


PERCENT = re.compile(r"[+\-−]?(\d+(?:\.\d+)?)\s?%")
DECIMAL = re.compile(r"(?<![\d.])[+\-−]?(\d+\.\d+)(?![\d.]*\s?%)")


def allowed_figures(facts: dict) -> set[str]:
    """The figures that may appear in commentary, without signs: '1.23%', '1.2%', '0.57'."""
    allowed = set()

    def add(value: str):
        if not isinstance(value, str):
            return
        for m in PERCENT.finditer(value):
            number = float(m.group(1))
            for digits in (0, 1, 2):                   # '1.23%' may be written '1.2%' or '1%'
                allowed.add(f"{number:.{digits}f}%")
            allowed.add(f"{m.group(1)}%")
        for m in DECIMAL.finditer(value):
            allowed.add(m.group(1))

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        else:
            add(node)

    walk(facts)
    return allowed


def unknown_figures(commentary: str, facts: dict) -> list[str]:
    """Figures in the commentary that are not in the report (the model made them up)."""
    allowed = allowed_figures(facts)
    found = [m.group(1) + "%" for m in PERCENT.finditer(commentary)]
    found += [m.group(1) for m in DECIMAL.finditer(commentary)]
    return sorted({f for f in found if f not in allowed})


# ---------- the session: state shared by the tools during one agent run ----------

def _status_from_db(engine) -> dict:
    sql = text("""
        SELECT s.strategy_id, s.name, s.benchmark_ticker, s.inception_date,
               MAX(f.return_date) AS latest_return_date
        FROM dim.strategies s
        LEFT JOIN fact.strategy_returns f USING (strategy_id)
        GROUP BY 1, 2, 3, 4 ORDER BY 1
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return {"today": date.today().isoformat(),
            "strategies": [{k: (v.isoformat() if isinstance(v, date) else v)
                            for k, v in row.items()} for row in rows]}


@dataclass
class ReportSession:
    """Holds the loaded report, the commentary and the files built during one run.

    The loaders are swappable so tests can run without a database.
    """
    report_loader: Callable[[str, date, date], ReportData] = build_report_data
    status_loader: Callable[[], dict] | None = None
    output_dir: Path | None = None
    report: ReportData | None = None
    facts: dict | None = None
    commentary: str | None = None
    files: dict[str, str] = field(default_factory=dict)

    def call(self, name: str, args: dict) -> dict:
        handlers = {
            "get_data_status": self.get_data_status,
            "get_report_numbers": self.get_report_numbers,
            "build_excel": self.build_excel,
            "write_commentary": self.write_commentary,
            "build_deck": self.build_deck,
        }
        if name not in handlers:
            raise ToolError(f"Unknown tool {name}.")
        return handlers[name](**args)

    # --- tools ---

    def get_data_status(self) -> dict:
        if self.status_loader:
            return self.status_loader()
        from report_agent.db import get_engine
        return _status_from_db(get_engine())

    def get_report_numbers(self, strategy_id: str, start: str, end: str) -> dict:
        try:
            start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ToolError(f"Dates must be YYYY-MM-DD: {exc}") from exc
        try:
            self.report = self.report_loader(strategy_id, start_d, end_d)
        except (ValueError, KeyError) as exc:
            raise ToolError(str(exc)) from exc
        last = self.report.daily.index.max()
        if pd.isna(last) or (pd.Timestamp(end_d) - last).days > 5:   # allow weekends/holidays
            self.report = None
            data_end = "no data" if pd.isna(last) else f"data ends {last:%Y-%m-%d}"
            raise ToolError(f"The period ends {end_d} but {data_end}. Pick an earlier period "
                            "or run the data pipeline first.")
        self.facts = report_facts(self.report)
        self.commentary = None                  # commentary belongs to one report
        return self.facts

    def build_excel(self) -> dict:
        report = self._require_report()
        path = build_excel(report, self._path(report, "xlsx"))
        self.files["excel"] = str(path)
        return {"path": str(path)}

    def write_commentary(self, text: str) -> dict:
        self._require_report()
        bad = unknown_figures(text, self.facts)
        if bad:
            raise ToolError("These figures are not in the report numbers: " + ", ".join(bad)
                            + ". Use only figures returned by get_report_numbers.")
        words = len(text.split())
        if not 60 <= words <= 250:
            raise ToolError(f"Commentary is {words} words; write 60 to 250.")
        self.commentary = text.strip()
        return {"saved": True, "words": words}

    def build_deck(self) -> dict:
        report = self._require_report()
        if not self.commentary:
            raise ToolError("No commentary saved yet. Call write_commentary first.")
        path = build_deck(report, self._path(report, "pptx"), commentary=self.commentary)
        self.files["deck"] = str(path)
        return {"path": str(path)}

    # --- helpers ---

    def _require_report(self) -> ReportData:
        if self.report is None:
            raise ToolError("No report loaded. Call get_report_numbers first.")
        return self.report

    def _path(self, report: ReportData, ext: str) -> Path | None:
        if self.output_dir is None:
            return None                         # each builder uses its default output/ path
        return self.output_dir / f"{report.strategy_id}_{report.start}_{report.end}.{ext}"
