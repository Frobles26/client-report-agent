"""Validate: clean the extracted prices and report every problem found."""

from dataclasses import dataclass, field

import pandas as pd

BIG_MOVE = 0.30  # a one-day move above 30% is flagged for review (kept, not dropped)


@dataclass
class ValidationReport:
    rows_in: int = 0
    rows_out: int = 0
    dropped_missing_or_bad: int = 0
    dropped_duplicates: int = 0
    big_moves: list[str] = field(default_factory=list)
    missing_days: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"{self.rows_out}/{self.rows_in} rows kept",
            f"{self.dropped_missing_or_bad} bad",
            f"{self.dropped_duplicates} duplicates",
        ]
        if self.big_moves:
            parts.append(f"{len(self.big_moves)} big moves flagged")
        if self.missing_days:
            parts.append(f"{len(self.missing_days)} tickers with missing days")
        return ", ".join(parts)


def validate_prices(prices: pd.DataFrame, calendar_ticker: str) -> tuple[pd.DataFrame, ValidationReport]:
    """Drop unusable rows, flag suspicious ones, and count missing trading days.

    calendar_ticker: the index, whose dates define the trading calendar.
    """
    report = ValidationReport(rows_in=len(prices))
    df = prices.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    bad = df["close"].isna() | (df["close"] <= 0) | df["ticker"].isna() | df["price_date"].isna()
    report.dropped_missing_or_bad = int(bad.sum())
    df = df[~bad]

    dupes = df.duplicated(subset=["ticker", "price_date"], keep="last")
    report.dropped_duplicates = int(dupes.sum())
    df = df[~dupes].sort_values(["ticker", "price_date"])

    moves = df.groupby("ticker")["close"].pct_change().abs()
    for _, row in df[moves > BIG_MOVE].iterrows():
        report.big_moves.append(f"{row['ticker']} {row['price_date']}")

    trading_days = set(df.loc[df["ticker"] == calendar_ticker, "price_date"])
    if trading_days:
        for ticker, dates in df.groupby("ticker")["price_date"]:
            first = dates.min()
            expected = {d for d in trading_days if d >= first}
            gap = len(expected - set(dates))
            if gap:
                report.missing_days[ticker] = gap

    report.rows_out = len(df)
    return df.reset_index(drop=True), report
