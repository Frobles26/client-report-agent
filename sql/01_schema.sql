-- Client Report Agent: database schema
-- Layers: raw (as received) -> dim (reference data) -> staging (clean) -> fact (calculated)
-- Every load is an upsert, so any script can be re-run safely.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS dim;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS fact;
CREATE SCHEMA IF NOT EXISTS ops;

-- ---------------------------------------------------------------
-- RAW: data exactly as the APIs returned it
-- ---------------------------------------------------------------

-- Daily closing prices for stocks and the benchmark index
CREATE TABLE IF NOT EXISTS raw.prices (
    ticker      TEXT        NOT NULL,
    price_date  DATE        NOT NULL,
    close       NUMERIC(18, 6) NOT NULL CHECK (close > 0),
    currency    CHAR(3)     NOT NULL DEFAULT 'CAD',
    source      TEXT        NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, price_date)
);

-- Bank of Canada Valet series: FX rates (e.g. FXUSDCAD) and the 3-month T-bill yield
CREATE TABLE IF NOT EXISTS raw.boc_series (
    series_id   TEXT        NOT NULL,
    obs_date    DATE        NOT NULL,
    value       NUMERIC(18, 6) NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, obs_date)
);

-- ---------------------------------------------------------------
-- DIM: reference data
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim.securities (
    ticker      TEXT PRIMARY KEY,
    name        TEXT    NOT NULL,
    sector      TEXT    NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'CAD',
    is_index    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS dim.strategies (
    strategy_id       TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    benchmark_ticker  TEXT NOT NULL REFERENCES dim.securities (ticker),
    inception_date    DATE NOT NULL,
    base_currency     CHAR(3) NOT NULL DEFAULT 'CAD'
);

-- ---------------------------------------------------------------
-- STAGING: cleaned data ready for calculations
-- ---------------------------------------------------------------

-- Portfolio weights at each month-end (fictional portfolio)
CREATE TABLE IF NOT EXISTS staging.holdings (
    strategy_id  TEXT NOT NULL REFERENCES dim.strategies (strategy_id),
    ticker       TEXT NOT NULL REFERENCES dim.securities (ticker),
    as_of_date   DATE NOT NULL,
    weight       NUMERIC(9, 6) NOT NULL CHECK (weight >= 0 AND weight <= 1),
    PRIMARY KEY (strategy_id, ticker, as_of_date)
);

-- Clean daily prices in CAD with the daily return already calculated
CREATE TABLE IF NOT EXISTS staging.daily_prices (
    ticker        TEXT NOT NULL REFERENCES dim.securities (ticker),
    price_date    DATE NOT NULL,
    close_cad     NUMERIC(18, 6) NOT NULL CHECK (close_cad > 0),
    daily_return  NUMERIC(12, 8),          -- NULL on the first day of history
    PRIMARY KEY (ticker, price_date)
);

-- ---------------------------------------------------------------
-- FACT: calculated results
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact.strategy_returns (
    strategy_id       TEXT NOT NULL REFERENCES dim.strategies (strategy_id),
    return_date       DATE NOT NULL,
    portfolio_return  NUMERIC(12, 8) NOT NULL,
    benchmark_return  NUMERIC(12, 8) NOT NULL,
    calculated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_id, return_date)
);

-- ---------------------------------------------------------------
-- OPS: a log of every pipeline and agent run
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.runs (
    run_id        BIGSERIAL PRIMARY KEY,
    job_name      TEXT        NOT NULL,           -- e.g. 'etl_prices', 'agent_report'
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT        NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'completed', 'failed')),
    rows_loaded   INTEGER,
    message       TEXT
);

CREATE INDEX IF NOT EXISTS ix_runs_job_started ON ops.runs (job_name, started_at DESC);
