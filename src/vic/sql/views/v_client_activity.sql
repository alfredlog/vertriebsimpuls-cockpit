-- Windows are half-open (start, end]: "last 90 days" includes as_of and
-- excludes as_of - 90, so the two windows never overlap.
CREATE OR REPLACE VIEW vic.v_client_activity AS
WITH trades_until_as_of AS (
    SELECT t.client_id, t.trade_date, t.notional_eur, rc.as_of
    FROM vic.fact_trades AS t
    CROSS JOIN vic.run_context AS rc
    WHERE t.trade_date <= rc.as_of
),

per_client AS (
    SELECT
        client_id,
        max(trade_date) AS last_trade_date,
        sum(notional_eur) FILTER (WHERE trade_date > as_of - 90)
            AS notional_last_90d_eur,
        sum(notional_eur) FILTER (
            WHERE trade_date > as_of - 180 AND trade_date <= as_of - 90
        ) AS notional_prev_90d_eur
    FROM trades_until_as_of
    GROUP BY client_id
)

-- Start from dim_client so clients without any trade still appear.
SELECT
    c.client_id,
    p.last_trade_date,
    date_diff('day', p.last_trade_date, rc.as_of) AS days_since_last_trade,
    coalesce(p.notional_last_90d_eur, 0) AS notional_last_90d_eur,
    coalesce(p.notional_prev_90d_eur, 0) AS notional_prev_90d_eur
FROM vic.dim_client AS c
CROSS JOIN vic.run_context AS rc
LEFT JOIN per_client AS p ON c.client_id = p.client_id;
