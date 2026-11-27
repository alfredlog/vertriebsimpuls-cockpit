-- Open means started on or before as_of and not yet matured; a position
-- maturing exactly 30 days after as_of is still included.
CREATE OR REPLACE VIEW vic.v_upcoming_maturities AS
SELECT
    p.position_id,
    p.client_id,
    p.product_id,
    p.maturity_date,
    p.notional_eur,
    date_diff('day', rc.as_of, p.maturity_date) AS days_to_maturity
FROM vic.fact_positions AS p
CROSS JOIN vic.run_context AS rc
WHERE p.start_date <= rc.as_of
    AND p.maturity_date > rc.as_of
    AND p.maturity_date <= rc.as_of + 30;
