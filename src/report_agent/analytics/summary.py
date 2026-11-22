"""Print a performance summary for a strategy and period: a quick check before any report.

Run: python -m report_agent.analytics.summary --start 2026-07-01 --end 2026-09-30
"""

import argparse
from datetime import date

import pandas as pd

from report_agent.analytics.report import build_report_data


def build_summary(strategy_id: str, start: date, end: date) -> dict[str, pd.DataFrame]:
    try:
        report = build_report_data(strategy_id, start, end)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return {
        "performance": report.performance,
        "risk": report.risk,
        "sectors": report.sectors,
        "top10": report.top10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a performance summary.")
    parser.add_argument("--strategy", default="CAN_EQ")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args()

    tables = build_summary(args.strategy, args.start, args.end)
    pct = {"portfolio": "{:.2%}", "benchmark": "{:.2%}", "active": "{:.2%}",
           "weight": "{:.2%}", "period_return": "{:.2%}", "contribution": "{:.2%}"}
    for name, table in tables.items():
        shown = table.copy()
        for col, fmt in pct.items():
            if col in shown and not (name == "risk" and col in ("portfolio", "benchmark")):
                shown[col] = shown[col].map(lambda v, f=fmt: "" if pd.isna(v) else f.format(v))
        if name == "risk":
            ratios = {"Sharpe ratio", "Beta", "Information ratio"}
            for col in ("portfolio", "benchmark"):
                shown[col] = [
                    "" if pd.isna(v) else (f"{v:.2f}" if stat in ratios else f"{v:.2%}")
                    for stat, v in zip(table["statistic"], table[col])
                ]
        title = "RISK (1 YEAR TO END DATE)" if name == "risk" else name.upper()
        print(f"\n== {title} ==")
        print(shown.to_string(index=False))


if __name__ == "__main__":
    main()
