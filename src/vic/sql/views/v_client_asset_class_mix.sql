-- Share is computed in DOUBLE: it is a ratio, not a money amount.
CREATE OR REPLACE VIEW vic.v_client_asset_class_mix AS
WITH last_12m AS (
    SELECT t.client_id, pr.asset_class, t.notional_eur
    FROM vic.fact_trades AS t
    INNER JOIN vic.dim_product AS pr ON t.product_id = pr.product_id
    CROSS JOIN vic.run_context AS rc
    WHERE t.trade_date > CAST(rc.as_of - INTERVAL 12 MONTH AS DATE)
        AND t.trade_date <= rc.as_of
),

per_class AS (
    SELECT client_id, asset_class, sum(notional_eur) AS notional_eur
    FROM last_12m
    GROUP BY client_id, asset_class
)

SELECT
    client_id,
    asset_class,
    notional_eur,
    CAST(notional_eur AS DOUBLE)
    / sum(CAST(notional_eur AS DOUBLE)) OVER (PARTITION BY client_id) AS share
FROM per_class;
