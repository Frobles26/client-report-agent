"""Portfolio composition: sector weights and top holdings. Pure pandas."""

import pandas as pd


def sector_weights(holdings: pd.DataFrame) -> pd.DataFrame:
    """Total weight per sector, largest first. holdings needs 'sector' and 'weight'."""
    out = (
        holdings.groupby("sector", as_index=False)["weight"].sum()
        .sort_values("weight", ascending=False, ignore_index=True)
    )
    out["holdings"] = holdings.groupby("sector")["ticker"].count().reindex(out["sector"]).to_numpy()
    return out


def top_holdings(holdings: pd.DataFrame, period_returns: pd.Series, n: int = 10) -> pd.DataFrame:
    """The n largest positions with their weight, return over the period and contribution.

    contribution = start weight x period return, a simple estimate of how much each
    position added to the portfolio's return.
    """
    top = holdings.nlargest(n, "weight")[["ticker", "name", "sector", "weight"]].copy()
    top["period_return"] = top["ticker"].map(period_returns)
    top["contribution"] = top["weight"] * top["period_return"]
    return top.reset_index(drop=True)
