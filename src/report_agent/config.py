"""Settings loaded from the environment (.env file locally, secrets in CI)."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://report_agent:change_me@localhost:5433/report_agent",
)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Bank of Canada Valet API (free, no key): https://www.bankofcanada.ca/valet/docs
BOC_VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"
BOC_SERIES = {
    "FXUSDCAD": "US dollar to Canadian dollar daily exchange rate",
    "TB.CDN.90D.MID": "3-month Government of Canada T-bill yield (risk-free rate)",
}

# Stock and index prices come from Yahoo Finance through the yfinance library
# (TSX tickers end in .TO; the S&P/TSX Composite index is ^GSPTSE).
BENCHMARK_TICKER = "^GSPTSE"
HISTORY_START = "2024-01-01"
