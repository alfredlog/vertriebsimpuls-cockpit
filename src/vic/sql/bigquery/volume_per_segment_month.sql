-- Traded volume per client segment and month, last 12 months up to @as_of.
-- The filter is on the partition column itself (no function around it), so
-- BigQuery only reads the monthly partitions of the window.
SELECT
    DATE_TRUNC(t.trade_date, MONTH) AS month,
    c.segment,
    COUNT(*) AS trades,
    SUM(t.notional_eur) AS volume_eur
FROM vic.fact_trades AS t
INNER JOIN vic.dim_client AS c ON t.client_id = c.client_id
WHERE t.trade_date > DATE_SUB(@as_of, INTERVAL 12 MONTH)
    AND t.trade_date <= @as_of
GROUP BY month, c.segment
ORDER BY month, c.segment
