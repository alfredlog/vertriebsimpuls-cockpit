-- Top 3 products per segment over the last 12 months, by number of clients
-- (one heavy client should not make a product look popular).
SELECT
    c.segment,
    pr.name AS product_name,
    COUNT(DISTINCT t.client_id) AS clients,
    SUM(t.notional_eur) AS volume_eur
FROM vic.fact_trades AS t
INNER JOIN vic.dim_client AS c ON t.client_id = c.client_id
INNER JOIN vic.dim_product AS pr ON t.product_id = pr.product_id
WHERE t.trade_date > DATE_SUB(@as_of, INTERVAL 12 MONTH)
    AND t.trade_date <= @as_of
GROUP BY c.segment, pr.product_id, pr.name
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY c.segment ORDER BY clients DESC, volume_eur DESC, pr.product_id
) <= 3
ORDER BY c.segment, clients DESC
