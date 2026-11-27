-- Open positions maturing within 30 days after @as_of (exactly 30 included).
-- DATE_DIFF takes (end, start, unit): the reverse of DuckDB's date_diff.
SELECT
    p.position_id,
    c.name AS client_name,
    pr.name AS product_name,
    p.maturity_date,
    p.notional_eur,
    DATE_DIFF(p.maturity_date, @as_of, DAY) AS days_to_maturity
FROM vic.fact_positions AS p
INNER JOIN vic.dim_client AS c ON p.client_id = c.client_id
INNER JOIN vic.dim_product AS pr ON p.product_id = pr.product_id
WHERE p.start_date <= @as_of
    AND p.maturity_date > @as_of
    AND p.maturity_date <= DATE_ADD(@as_of, INTERVAL 30 DAY)
ORDER BY p.maturity_date, p.position_id
