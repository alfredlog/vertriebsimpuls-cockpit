-- Rates exist only on business days, so each comparison point takes the
-- latest observation on or before it (as_of, as_of - 14 days, as_of - 1 month).
-- A change is NULL when no observation exists before that point.
CREATE OR REPLACE VIEW vic.v_rate_changes AS
WITH points AS (
    SELECT
        r.tenor_years,
        max(r.rate_date) FILTER (WHERE r.rate_date <= rc.as_of) AS rate_date,
        arg_max(r.rate_pct, r.rate_date) FILTER (WHERE r.rate_date <= rc.as_of)
            AS rate_pct,
        arg_max(r.rate_pct, r.rate_date) FILTER (WHERE r.rate_date <= rc.as_of - 14)
            AS rate_14d_ago_pct,
        arg_max(r.rate_pct, r.rate_date) FILTER (
            WHERE r.rate_date <= CAST(rc.as_of - INTERVAL 1 MONTH AS DATE)
        ) AS rate_1m_ago_pct
    FROM vic.market_rates AS r
    CROSS JOIN vic.run_context AS rc
    GROUP BY r.tenor_years
)

SELECT
    tenor_years,
    rate_date,
    rate_pct,
    rate_14d_ago_pct,
    (rate_pct - rate_14d_ago_pct) * 100 AS change_14d_bp,
    rate_1m_ago_pct,
    (rate_pct - rate_1m_ago_pct) * 100 AS change_1m_bp
FROM points;
