"""Business rules that explain *why now* a client should be contacted.

Every rule is a function (con, as_of) -> DataFrame with SIGNAL_COLUMNS.
reason_de is rendered only from the values in facts (via format_fact), so the
text can never state a number that is not stored next to it.
"""

from datetime import date

import duckdb
import pandas as pd

from vic.db import query_df

SIGNAL_COLUMNS = [
    "client_id",
    "product_id",
    "signal_type",
    "signal_strength",
    "reason_de",
    "facts",
]

# Strength of the weakest signal that still fires; the strongest is 1.0.
MIN_STRENGTH = 0.5

# The feature views have fixed windows (30 days maturity, 14 days rates,
# 90 days activity, 12 months mix). The window constants below repeat them for
# the reason texts; thresholds must not exceed them or candidates get cut off.
MATURITY_WINDOW_DAYS = 30

RATE_MOVE_TENORS = (5, 10)
RATE_MOVE_WINDOW_DAYS = 14
RATE_MOVE_THRESHOLD_BP = 20.0
RATE_MOVE_FULL_STRENGTH_BP = 40.0
# Rising rates: lock in a fixed rate before further increases.
# Falling rates: stay floating, but cap the risk of a rebound.
RATE_RISING_PRODUCT_ID = "P008"  # Zinsswap 5J
RATE_FALLING_PRODUCT_ID = "P010"  # Zinscap 3J

REACTIVATION_INACTIVE_DAYS = 90
REACTIVATION_MAX_DAYS = 180

CONCENTRATION_WINDOW_MONTHS = 12
CONCENTRATION_THRESHOLD = 0.60
CONCENTRATION_MIN_SEGMENT_PENETRATION = 0.20

# Highest product risk level (1-5) a client may be offered per risk profile.
MAX_RISK_LEVEL = {"conservative": 2, "balanced": 3, "dynamic": 5}

# Hedges skip the risk-profile check (a hedge lowers the client's risk); their
# condition is an exposure to hedge instead. Rates hedges need floating-rate
# debt. FX hedges stay allowed: the data has no FX exposure to check against.
HEDGE_ASSET_CLASS_NEEDING_FLOATING_DEBT = "rates_derivative"

SUITABLE_PRODUCTS_CTE = """
suitable_products AS (
    SELECT c.client_id, p.product_id
    FROM vic.dim_client AS c
    CROSS JOIN vic.dim_product AS p
    WHERE (
        p.is_hedging_product
        AND (p.asset_class != $rates_hedge_class OR c.has_floating_rate_debt)
    )
    OR (
        NOT p.is_hedging_product
        AND p.risk_level <= CASE c.risk_profile
            WHEN 'conservative' THEN $max_risk_conservative
            WHEN 'balanced' THEN $max_risk_balanced
            WHEN 'dynamic' THEN $max_risk_dynamic
        END
    )
)
"""

ASSET_CLASS_LABELS_DE = {
    "government_bond": "Staatsanleihen",
    "covered_bond": "Pfandbriefe",
    "corporate_bond": "Unternehmensanleihen",
    "rates_derivative": "Zinsderivate",
    "fx_derivative": "Devisenderivate",
    "money_market": "Geldmarktprodukte",
    "structured_product": "strukturierte Produkte",
    "fund": "Fonds",
}


# --- Formatting --------------------------------------------------------------


def format_eur_mio(value: float) -> str:
    """Format an EUR amount in German style, e.g. 4500000 -> '4,5 Mio. EUR'."""
    english = f"{value / 1e6:,.1f}"
    german = english.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{german} Mio. EUR"


def format_fact(key: str, value: object) -> str:
    """Format one fact for German text; the key suffix decides the format."""
    if key.endswith("_date"):
        return value.strftime("%d.%m.%Y")
    if key.endswith("_eur"):
        return format_eur_mio(value)
    if key.endswith("_bp"):
        return f"{value:+.0f} bp"
    if key.endswith("_pct"):
        return f"{value:.0f} %"
    if key.endswith(("_days", "_months", "_years")):
        return str(int(value))
    return str(value)


def _render(template: str, facts: dict) -> str:
    """Fill a German reason template with formatted facts."""
    return template.format(**{key: format_fact(key, v) for key, v in facts.items()})


# --- Helpers -----------------------------------------------------------------


def _linear_strength(value: float, strongest_at: float, weakest_at: float) -> float:
    """Map value linearly to 1.0 at strongest_at and MIN_STRENGTH at weakest_at."""
    position = (value - strongest_at) / (weakest_at - strongest_at)
    position = min(max(position, 0.0), 1.0)
    return 1.0 - (1.0 - MIN_STRENGTH) * position


def _check_run_context(con: duckdb.DuckDBPyConnection, as_of: date) -> None:
    """Fail if the views were built for another date than the requested as_of."""
    stored = con.execute("SELECT as_of FROM vic.run_context").fetchone()
    if stored is None or stored[0] != as_of:
        raise ValueError(f"run_context holds {stored}, rules were called for {as_of}")


def _suitability_params() -> dict[str, int | str]:
    """Return the named SQL parameters used by SUITABLE_PRODUCTS_CTE."""
    params: dict[str, int | str] = {
        f"max_risk_{profile}": level for profile, level in MAX_RISK_LEVEL.items()
    }
    params["rates_hedge_class"] = HEDGE_ASSET_CLASS_NEEDING_FLOATING_DEBT
    return params


def suitable_products(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return every (client_id, product_id) pair the client may be offered.

    Shared with the pipeline so recommender candidates follow the same rules.
    """
    sql = (
        "WITH"
        + SUITABLE_PRODUCTS_CTE
        + "SELECT client_id, product_id FROM suitable_products"
    )
    return query_df(con, sql, _suitability_params())


def _to_frame(signals: list[dict]) -> pd.DataFrame:
    """Build the signal DataFrame, also with the right columns when empty."""
    return pd.DataFrame(signals, columns=SIGNAL_COLUMNS)


# --- Rules -------------------------------------------------------------------

MATURITY_SQL = (
    "WITH"
    + SUITABLE_PRODUCTS_CTE
    + """,

ranked_products AS (
    SELECT lt.client_id, lt.product_id, p.name, p.asset_class
    FROM vic.v_client_product_last_trade AS lt
    INNER JOIN vic.dim_product AS p ON lt.product_id = p.product_id
    INNER JOIN suitable_products AS sp
        ON lt.client_id = sp.client_id AND lt.product_id = sp.product_id
    QUALIFY row_number() OVER (
        PARTITION BY lt.client_id, p.asset_class
        ORDER BY lt.trade_count DESC, lt.total_notional_eur DESC, lt.product_id
    ) = 1
)

SELECT
    m.position_id,
    m.client_id,
    m.maturity_date,
    CAST(m.notional_eur AS DOUBLE) AS notional_eur,
    m.days_to_maturity,
    mp.name AS matured_product_name,
    -- Without suitable trades in that asset class, reinvest in the same product.
    coalesce(r.product_id, m.product_id) AS proposed_product_id,
    coalesce(r.name, mp.name) AS proposed_product_name
FROM vic.v_upcoming_maturities AS m
INNER JOIN vic.dim_product AS mp ON m.product_id = mp.product_id
LEFT JOIN ranked_products AS r
    ON m.client_id = r.client_id AND mp.asset_class = r.asset_class
WHERE m.days_to_maturity <= $window_days
    -- The fallback product must be suitable too, otherwise no signal.
    AND EXISTS (
        SELECT 1 FROM suitable_products AS sp
        WHERE sp.client_id = m.client_id
            AND sp.product_id = coalesce(r.product_id, m.product_id)
    )
ORDER BY m.client_id, m.maturity_date, m.position_id
"""
)

MATURITY_TEMPLATE_SAME = (
    "Fälligkeit am {maturity_date}: {notional_eur} {matured_product_name}"
    " – Wiederanlage ansprechen."
)
MATURITY_TEMPLATE_OTHER = (
    "Fälligkeit am {maturity_date}: {notional_eur} {matured_product_name}"
    " – Wiederanlage in {proposed_product_name} ansprechen."
)


def maturity_rule(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Positions maturing within 30 days: propose a reinvestment.

    The proposal is the client's most traded suitable product of the same
    asset class, so the reinvestment matches what the client already knows.
    """
    _check_run_context(con, as_of)
    params = {"window_days": MATURITY_WINDOW_DAYS, **_suitability_params()}
    rows = query_df(con, MATURITY_SQL, params)
    signals = []
    for row in rows.itertuples():
        facts = {
            "position_id": row.position_id,
            "maturity_date": row.maturity_date.date(),
            "notional_eur": row.notional_eur,
            "matured_product_name": row.matured_product_name,
            "proposed_product_name": row.proposed_product_name,
            "days_to_maturity": int(row.days_to_maturity),
        }
        same = row.matured_product_name == row.proposed_product_name
        template = MATURITY_TEMPLATE_SAME if same else MATURITY_TEMPLATE_OTHER
        signals.append(
            {
                "client_id": row.client_id,
                "product_id": row.proposed_product_id,
                "signal_type": "maturity",
                "signal_strength": _linear_strength(
                    row.days_to_maturity, 0, MATURITY_WINDOW_DAYS
                ),
                "reason_de": _render(template, facts),
                "facts": facts,
            }
        )
    return _to_frame(signals)


RATE_MOVE_SQL = """
SELECT CAST(tenor_years AS INTEGER) AS tenor_years,
       CAST(change_14d_bp AS DOUBLE) AS change_bp
FROM vic.v_rate_changes
WHERE list_contains(?, CAST(tenor_years AS INTEGER))
    AND abs(change_14d_bp) > ?
-- The biggest move drives the signal; on a tie the shorter tenor wins.
ORDER BY abs(change_14d_bp) DESC, tenor_years
LIMIT 1
"""

RATE_MOVE_TEMPLATE = (
    "{tenor_years}J-Zins in {window_days} Tagen um {change_bp} {direction};"
    " variable Finanzierung – {product_name} ansprechen."
)


def rate_move_rule(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Big 5Y/10Y rate move: propose a hedge to clients with floating-rate debt.

    No risk-profile filter: hedges are exempt (see SUITABLE_PRODUCTS_CTE), and
    the floating-rate debt condition is applied here directly.
    """
    _check_run_context(con, as_of)
    move = query_df(
        con, RATE_MOVE_SQL, [list(RATE_MOVE_TENORS), RATE_MOVE_THRESHOLD_BP]
    )
    if move.empty:
        return _to_frame([])

    change_bp = float(move.loc[0, "change_bp"])
    rising = change_bp > 0
    product_id = RATE_RISING_PRODUCT_ID if rising else RATE_FALLING_PRODUCT_ID
    product_name = con.execute(
        "SELECT name FROM vic.dim_product WHERE product_id = ?", [product_id]
    ).fetchone()[0]
    clients = query_df(
        con,
        "SELECT client_id FROM vic.dim_client WHERE has_floating_rate_debt"
        " ORDER BY client_id",
    )

    facts = {
        "tenor_years": int(move.loc[0, "tenor_years"]),
        "window_days": RATE_MOVE_WINDOW_DAYS,
        "change_bp": round(change_bp, 2),
        "direction": "gestiegen" if rising else "gefallen",
        "product_name": product_name,
    }
    strength = min(1.0, abs(change_bp) / RATE_MOVE_FULL_STRENGTH_BP)
    reason = _render(RATE_MOVE_TEMPLATE, facts)
    signals = [
        {
            "client_id": client_id,
            "product_id": product_id,
            "signal_type": "rate_move",
            "signal_strength": strength,
            "reason_de": reason,
            "facts": dict(facts),
        }
        for client_id in clients["client_id"]
    ]
    return _to_frame(signals)


REACTIVATION_SQL = """
SELECT client_id,
       last_trade_date,
       days_since_last_trade,
       CAST(notional_prev_90d_eur AS DOUBLE) AS notional_prev_90d_eur
FROM vic.v_client_activity
WHERE days_since_last_trade > ?
    AND notional_prev_90d_eur > 0
ORDER BY client_id
"""

REACTIVATION_TEMPLATE = (
    "Seit {inactive_days} Tagen kein Geschäft (letztes am {last_trade_date}),"
    " davor {prev_notional_eur} in {window_days} Tagen – Kontakt reaktivieren."
)


def reactivation_rule(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Clients silent for > 90 days that traded in the 90 days before that.

    product_id stays empty: the rule says *why now*, the recommender says *what*.
    """
    _check_run_context(con, as_of)
    rows = query_df(con, REACTIVATION_SQL, [REACTIVATION_INACTIVE_DAYS])
    signals = []
    for row in rows.itertuples():
        facts = {
            "inactive_days": int(row.days_since_last_trade),
            "last_trade_date": row.last_trade_date.date(),
            "prev_notional_eur": row.notional_prev_90d_eur,
            "window_days": REACTIVATION_INACTIVE_DAYS,
        }
        signals.append(
            {
                "client_id": row.client_id,
                "product_id": None,
                "signal_type": "reactivation",
                "signal_strength": _linear_strength(
                    row.days_since_last_trade,
                    REACTIVATION_INACTIVE_DAYS,
                    REACTIVATION_MAX_DAYS,
                ),
                "reason_de": _render(REACTIVATION_TEMPLATE, facts),
                "facts": facts,
            }
        )
    return _to_frame(signals)


CONCENTRATION_SQL = (
    "WITH"
    + SUITABLE_PRODUCTS_CTE
    + """,

concentrated AS (
    SELECT client_id, asset_class, share
    FROM vic.v_client_asset_class_mix
    WHERE share > $threshold
),

-- Penetration: share of the segment's clients that ever traded the class.
segment_size AS (
    SELECT segment, count(*) AS n_clients
    FROM vic.dim_client
    GROUP BY segment
),

-- Hedging products are not a diversification of investments and only make
-- sense with an exposure to hedge; rate_move covers them.
investment_trades AS (
    SELECT lt.client_id, c.segment, p.asset_class, p.product_id, p.name
    FROM vic.v_client_product_last_trade AS lt
    INNER JOIN vic.dim_client AS c ON lt.client_id = c.client_id
    INNER JOIN vic.dim_product AS p ON lt.product_id = p.product_id
    WHERE NOT p.is_hedging_product
),

class_penetration AS (
    SELECT t.segment, t.asset_class,
           count(DISTINCT t.client_id) / any_value(s.n_clients) AS penetration
    FROM investment_trades AS t
    INNER JOIN segment_size AS s ON t.segment = s.segment
    GROUP BY t.segment, t.asset_class
),

product_popularity AS (
    SELECT segment, asset_class, product_id, name,
           count(DISTINCT client_id) AS n_clients
    FROM investment_trades
    GROUP BY segment, asset_class, product_id, name
)

SELECT
    conc.client_id,
    CAST(conc.asset_class AS VARCHAR) AS top_asset_class,
    conc.share AS top_share,
    CAST(cp.asset_class AS VARCHAR) AS proposed_asset_class,
    cp.penetration,
    pp.product_id AS proposed_product_id,
    pp.name AS proposed_product_name
FROM concentrated AS conc
INNER JOIN vic.dim_client AS c ON conc.client_id = c.client_id
INNER JOIN class_penetration AS cp ON c.segment = cp.segment
INNER JOIN product_popularity AS pp
    ON cp.segment = pp.segment AND cp.asset_class = pp.asset_class
-- Only products this client may be offered; a class without one is skipped.
INNER JOIN suitable_products AS sp
    ON conc.client_id = sp.client_id AND pp.product_id = sp.product_id
WHERE cp.penetration >= $min_penetration
    -- "Missing" means no trade in that class within the 12-month mix window.
    AND NOT EXISTS (
        SELECT 1 FROM vic.v_client_asset_class_mix AS mix
        WHERE mix.client_id = conc.client_id AND mix.asset_class = cp.asset_class
    )
-- Most popular class first (on a tie the class declared first in the ENUM),
-- then the segment's most popular suitable product in that class.
QUALIFY row_number() OVER (
    PARTITION BY conc.client_id
    ORDER BY cp.penetration DESC, cp.asset_class, pp.n_clients DESC, pp.product_id
) = 1
ORDER BY conc.client_id
"""
)

CONCENTRATION_TEMPLATE = (
    "{top_share_pct} des Volumens der letzten {window_months} Monate entfallen auf"
    " {top_class_label}; {segment_penetration_pct} der Kunden im Segment handeln"
    " {proposed_class_label} – {proposed_product_name} zur Diversifikation ansprechen."
)


def concentration_rule(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """One asset class > 60 % of 12-month notional: propose a missing class.

    The proposed class is the one most clients of the same segment trade, so
    the idea is proven for comparable clients. Only classes with a product
    that suits the client's risk profile are considered.
    """
    _check_run_context(con, as_of)
    params = {
        "threshold": CONCENTRATION_THRESHOLD,
        "min_penetration": CONCENTRATION_MIN_SEGMENT_PENETRATION,
        **_suitability_params(),
    }
    rows = query_df(con, CONCENTRATION_SQL, params)
    signals = []
    for row in rows.itertuples():
        facts = {
            "top_share_pct": row.top_share * 100,
            "window_months": CONCENTRATION_WINDOW_MONTHS,
            "top_class_label": ASSET_CLASS_LABELS_DE[row.top_asset_class],
            "segment_penetration_pct": row.penetration * 100,
            "proposed_class_label": ASSET_CLASS_LABELS_DE[row.proposed_asset_class],
            "proposed_product_name": row.proposed_product_name,
        }
        signals.append(
            {
                "client_id": row.client_id,
                "product_id": row.proposed_product_id,
                "signal_type": "concentration",
                "signal_strength": _linear_strength(
                    row.top_share, 1.0, CONCENTRATION_THRESHOLD
                ),
                "reason_de": _render(CONCENTRATION_TEMPLATE, facts),
                "facts": facts,
            }
        )
    return _to_frame(signals)


RULES = (maturity_rule, rate_move_rule, reactivation_rule, concentration_rule)


def run_all_rules(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Run every rule and return all signals in one DataFrame."""
    frames = [rule(con, as_of) for rule in RULES]
    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return _to_frame([])
    return pd.concat(non_empty, ignore_index=True)
