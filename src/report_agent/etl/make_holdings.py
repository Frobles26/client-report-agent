"""Create the fictional portfolio: month-end weights for every stock in securities.csv.

The weights are made up but realistic: they start uneven, drift a little every month,
and always add up to exactly 100%. A fixed random seed means everyone who runs this
gets the same portfolio.

Run: python -m report_agent.etl.make_holdings
"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from report_agent.config import HISTORY_START

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "sample"
STRATEGY_ID = "CAN_EQ"
SEED = 42
MONTHLY_DRIFT = 0.05  # each weight moves about +/-5% (relative) per month
DECIMALS = 6          # matches NUMERIC(9, 6) in staging.holdings


def month_ends(start: str, today: date) -> list[date]:
    """Month-ends from the one before `start` up to the last completed month."""
    first = pd.Timestamp(start) - pd.offsets.MonthEnd(1)
    last = pd.Timestamp(today) - pd.offsets.MonthEnd(1)
    return [d.date() for d in pd.date_range(first, last, freq="ME")]


def round_to_one(weights: np.ndarray, decimals: int = DECIMALS) -> np.ndarray:
    """Round weights and put any rounding difference on the largest one, so they sum to 1."""
    rounded = np.round(weights / weights.sum(), decimals)
    rounded[np.argmax(rounded)] += round(1 - rounded.sum(), decimals)
    return np.round(rounded, decimals)


def make_holdings(tickers: list[str], dates: list[date], seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    weights = rng.dirichlet(np.full(len(tickers), 2.0))
    rows = []
    for as_of in dates:
        weights = weights * (1 + rng.normal(0, MONTHLY_DRIFT, len(tickers)))
        weights = np.clip(weights, 0.002, None)
        final = round_to_one(weights)
        rows += [
            {"strategy_id": STRATEGY_ID, "ticker": t, "as_of_date": as_of, "weight": w}
            for t, w in zip(tickers, final)
        ]
    return pd.DataFrame(rows)


def main() -> None:
    securities = pd.read_csv(DATA_DIR / "securities.csv")
    tickers = securities.loc[~securities["is_index"], "ticker"].tolist()
    holdings = make_holdings(tickers, month_ends(HISTORY_START, date.today()))
    out = DATA_DIR / "holdings.csv"
    holdings.to_csv(out, index=False)
    print(f"Wrote {len(holdings)} rows ({holdings['as_of_date'].nunique()} month-ends) to {out}")


if __name__ == "__main__":
    main()
