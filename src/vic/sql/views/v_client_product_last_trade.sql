CREATE OR REPLACE VIEW vic.v_client_product_last_trade AS
SELECT
    t.client_id,
    t.product_id,
    max(t.trade_date) AS last_trade_date,
    count(*) AS trade_count,
    sum(t.notional_eur) AS total_notional_eur
FROM vic.fact_trades AS t
CROSS JOIN vic.run_context AS rc
-- Trades after as_of would leak the future into a backtest run.
WHERE t.trade_date <= rc.as_of
GROUP BY t.client_id, t.product_id;
