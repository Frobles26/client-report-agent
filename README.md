# Client Report Agent

An AI agent that turns one plain-English request into a checked Excel workbook and PowerPoint performance pack for an investment strategy.

> Build the Q3 2026 performance pack for the Canadian Equity strategy vs. the S&P/TSX Composite, for a pension client.

**Status:** in progress. Done: setup and schema (Day 1), data pipeline (Day 2), return and risk analytics (Day 3).

## Load the data

```bash
python -m report_agent.etl.make_holdings   # creates the fictional portfolio (data/sample/holdings.csv)
python -m report_agent.etl.run_pipeline    # first run loads history from 2024-01-01
```

After the first run, `run_pipeline` reloads only the last 10 days, then recalculates the daily strategy returns in `fact.strategy_returns`. Every step is recorded in `ops.runs`.

## Check the numbers

```bash
python -m report_agent.analytics.summary --start 2026-07-01 --end 2026-09-30
```

Prints the performance table (period, year to date, 1 year, since inception), risk statistics over the year to the end date (volatility, Sharpe ratio, max drawdown, beta, tracking error, information ratio), sector weights and the top 10 holdings.

Methods: returns are compounded daily and annualized only for periods over one year; risk statistics use 252 trading days; the risk-free rate is the Bank of Canada 3-month T-bill yield. All returns are price returns (dividends excluded) for both the portfolio and the index.

## How it works

1. **Data pipeline (no AI):** pulls daily TSX prices (Yahoo Finance via `yfinance`) and Bank of Canada FX and T-bill rates (Valet API), validates them and loads PostgreSQL in layers: `raw` → `staging` → `fact`.
2. **Report agent:** an LLM plans the work and calls tested Python tools that query the database, calculate performance and risk, build the Excel and PowerPoint files, and run quality checks. The LLM never calculates numbers itself.

The portfolio is fictional; the market data is real and public.

## Setup

Requirements: Python 3.11+, Git, and a PostgreSQL database (local with Docker, or a free cloud database such as Neon).

```bash
git clone https://github.com/<your-username>/client-report-agent.git
cd client-report-agent
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # Windows: copy .env.example .env
```

Then choose a database:

- **Local with Docker:** `docker compose up -d` starts PostgreSQL and loads the schema.
- **Cloud (no install):** create a free PostgreSQL database (for example on Neon), put its connection string in `.env` as `DATABASE_URL=postgresql+psycopg://...`, then run `python -m report_agent.setup_db`.

Check it and run the tests:

```bash
python -m report_agent.db     # should print "Database OK"
pytest
```

## Project layout

```
sql/                  schema and views
src/report_agent/
  etl/                extract, validate, load
  analytics/          returns, risk, weights
  outputs/            Excel, charts, PowerPoint
  checks/             quality checks
  agent/              the agent loop and its tools
tests/                pytest
```
