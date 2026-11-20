"""Extract: download prices and Bank of Canada series. No database code here."""

import logging
import time

import pandas as pd
import requests

from report_agent.config import BOC_VALET_URL

log = logging.getLogger(__name__)


class ExtractError(RuntimeError):
    """Raised when a source returns nothing usable."""


def fetch_prices(tickers: list[str], start: str, end: str | None = None) -> pd.DataFrame:
    """Daily closing prices from Yahoo Finance via yfinance.

    Returns one row per ticker and date: ticker, price_date, close.
    Uses the split-adjusted close (not dividend-adjusted), so stocks and the index,
    which is a price index, are measured the same way.
    """
    import yfinance as yf  # imported here so tests can run without it

    data = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data is None or data.empty:
        raise ExtractError("Yahoo Finance returned no data. Check the tickers and your connection.")
    return _close_to_long(data, tickers)


def _close_to_long(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Turn yfinance's wide table (one column per ticker) into long rows."""
    if isinstance(data.columns, pd.MultiIndex):
        close = data["Close"]
    else:  # a single ticker comes back without the ticker level
        close = data[["Close"]].rename(columns={"Close": tickers[0]})
    long = (
        close.rename_axis("price_date")
        .reset_index()
        .melt(id_vars="price_date", var_name="ticker", value_name="close")
        .dropna(subset=["close"])
    )
    long["price_date"] = pd.to_datetime(long["price_date"]).dt.date
    missing = sorted(set(tickers) - set(long["ticker"]))
    if missing:
        log.warning("No prices returned for: %s", ", ".join(missing))
    return long[["ticker", "price_date", "close"]]


def fetch_boc_series(series_id: str, start: str, retries: int = 3) -> pd.DataFrame:
    """One Bank of Canada Valet series: series_id, obs_date, value."""
    url = BOC_VALET_URL.format(series=series_id)
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params={"start_date": start}, timeout=30)
            if resp.status_code == 404:
                raise ExtractError(f"Bank of Canada series '{series_id}' does not exist.")
            resp.raise_for_status()
            return parse_boc_json(resp.json(), series_id)
        except requests.RequestException as exc:
            if attempt == retries:
                raise ExtractError(f"Bank of Canada request failed for {series_id}: {exc}") from exc
            wait = 2 ** attempt
            log.warning("Retrying %s in %ss (%s)", series_id, wait, exc)
            time.sleep(wait)
    raise AssertionError("unreachable")


def parse_boc_json(payload: dict, series_id: str) -> pd.DataFrame:
    """Valet format: {"observations": [{"d": "2024-01-02", "<series_id>": {"v": "1.33"}}, ...]}"""
    if series_id not in payload.get("seriesDetail", {}):
        raise ExtractError(f"Response does not contain series '{series_id}'.")
    rows = []
    for obs in payload.get("observations", []):
        value = obs.get(series_id, {}).get("v")
        if value in (None, ""):
            continue  # holidays and gaps come back empty
        rows.append({"series_id": series_id, "obs_date": obs["d"], "value": float(value)})
    df = pd.DataFrame(rows, columns=["series_id", "obs_date", "value"])
    df["obs_date"] = pd.to_datetime(df["obs_date"]).dt.date
    return df
