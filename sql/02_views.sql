-- Helper views used by the analytics tools.

-- Weights that apply on each trading day: each strategy's latest month-end weights
-- dated strictly before that day (so a day never uses weights it could not have known).
CREATE OR REPLACE VIEW staging.v_daily_weights AS
SELECT
    d.price_date,
    s.strategy_id,
    h.ticker,
    h.weight
FROM (SELECT DISTINCT price_date FROM staging.daily_prices) d
CROSS JOIN dim.strategies s
JOIN LATERAL (
    SELECT MAX(as_of_date) AS as_of_date
    FROM staging.holdings
    WHERE strategy_id = s.strategy_id
      AND as_of_date < d.price_date
) latest ON latest.as_of_date IS NOT NULL
JOIN staging.holdings h
  ON h.strategy_id = s.strategy_id
 AND h.as_of_date  = latest.as_of_date;

-- Weight check: total_weight should be 1.000000 for every strategy and month-end
CREATE OR REPLACE VIEW staging.v_weight_totals AS
SELECT strategy_id, as_of_date, SUM(weight) AS total_weight
FROM staging.holdings
GROUP BY strategy_id, as_of_date;
