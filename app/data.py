"""Data access for the Dash app; every query goes through vic.db helpers.

Which database is used is decided at call time, never at import time:
an explicit connection from set_connection() wins (tests: in-memory states),
otherwise the file at $VIC_DB_PATH (default data/vic.duckdb).

File connections are short-lived: opened per query and closed right after.
DuckDB allows only one process per file, so a connection kept open would lock
the nightly pipeline (another container) out. Reopening costs ~15 ms. Inside
the app process the lock serialises the gunicorn threads, because DuckDB also
refuses a second attach of the same file within one process.
"""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from vic.db import DEFAULT_DB_PATH, get_connection, query_df, record_feedback

DB_PATH_ENV = "VIC_DB_PATH"
# Longer than a pipeline run (~3 s): a request during the nightly run waits
# instead of failing.
LOCK_TIMEOUT_S = 15.0

_lock = threading.Lock()
_override: duckdb.DuckDBPyConnection | None = None


def db_path() -> Path:
    """Return the database file the app uses, read from the environment now."""
    return Path(os.environ.get(DB_PATH_ENV, DEFAULT_DB_PATH))


def set_connection(con: duckdb.DuckDBPyConnection | None) -> None:
    """Use the given connection instead of the file; None removes the override."""
    global _override
    with _lock:
        _override = con


def reset_connection() -> None:
    """Drop the override; the next query opens the file again."""
    set_connection(None)


@contextmanager
def _connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield the override, or a file connection that is closed afterwards."""
    with _lock:
        if _override is not None:
            yield _override
            return
        con = get_connection(db_path(), lock_timeout_s=LOCK_TIMEOUT_S)
        try:
            yield con
        finally:
            con.close()


def _query(sql: str, params: list | dict | None = None) -> pd.DataFrame:
    with _connection() as con:
        return query_df(con, sql, params)


def has_tables(*qualified_names: str) -> bool:
    """True if every given 'schema.table' exists (fresh clone: none do).

    Pages call this before querying, so a database that the daily run has not
    built yet shows a hint instead of an error.
    """
    existing = _query(
        """
        SELECT table_schema || '.' || table_name AS name
        FROM information_schema.tables
        WHERE table_catalog = current_database()
        """
    )["name"]
    return set(qualified_names) <= set(existing)


def has_impulses() -> bool:
    """True if the daily pipeline has written impulses at least once."""
    if not has_tables("vic_app.daily_impulses"):
        return False
    return bool(_query("SELECT count(*) AS n FROM vic_app.daily_impulses")["n"].iloc[0])


def latest_run_date() -> date | None:
    """Return the most recent run_date with impulses."""
    value = _query("SELECT max(run_date) AS d FROM vic_app.daily_impulses")["d"].iloc[0]
    return None if pd.isna(value) else pd.Timestamp(value).date()


def sales_reps() -> pd.DataFrame:
    """Return all sales reps (id, name)."""
    return _query("SELECT sales_rep_id, name FROM vic.dim_sales_rep ORDER BY name")


LATEST_FEEDBACK_CTE = """
latest_feedback AS (
    SELECT impulse_id, arg_max(status, ts) AS status
    FROM vic_app.impulse_feedback
    GROUP BY impulse_id
)
"""


def impulses(
    run_date: date,
    sales_rep_id: str | None = None,
    segment: str | None = None,
    signal_type: str | None = None,
) -> pd.DataFrame:
    """Return the impulses of run_date with names and the latest feedback status."""
    sql = (
        "WITH"
        + LATEST_FEEDBACK_CTE
        + """
SELECT i.impulse_id, i.run_date, i.rank, i.sales_rep_id, r.name AS rep_name,
       i.client_id, c.name AS client_name, CAST(c.segment AS VARCHAR) AS segment,
       i.product_id, p.name AS product_name, i.signal_type, i.score, i.reason_de,
       f.status
FROM vic_app.daily_impulses AS i
LEFT JOIN vic.dim_client AS c ON i.client_id = c.client_id
LEFT JOIN vic.dim_product AS p ON i.product_id = p.product_id
LEFT JOIN vic.dim_sales_rep AS r ON i.sales_rep_id = r.sales_rep_id
LEFT JOIN latest_feedback AS f ON i.impulse_id = f.impulse_id
WHERE i.run_date = $run_date
    AND ($rep::VARCHAR IS NULL OR i.sales_rep_id = $rep)
    AND ($segment::VARCHAR IS NULL OR CAST(c.segment AS VARCHAR) = $segment)
    AND ($signal::VARCHAR IS NULL OR i.signal_type = $signal)
ORDER BY r.name, i.rank
"""
    )
    params = {
        "run_date": run_date,
        "rep": sales_rep_id,
        "segment": segment,
        "signal": signal_type,
    }
    return _query(sql, params)


def save_feedback(impulse_id: str, run_date: date, status: str, ts: datetime) -> None:
    """Store a sales rep's decision on one impulse."""
    with _connection() as con:
        record_feedback(con, impulse_id, run_date, status, ts)


# --- Kunde 360 -------------------------------------------------------------------


def client(client_id: str) -> pd.Series | None:
    """Return the master data of one client, or None if unknown."""
    rows = _query(
        """
        SELECT c.client_id, c.name, CAST(c.segment AS VARCHAR) AS segment, c.region,
               CAST(c.aum_eur AS DOUBLE) AS aum_eur,
               CAST(c.risk_profile AS VARCHAR) AS risk_profile,
               c.has_floating_rate_debt, r.name AS rep_name
        FROM vic.dim_client AS c
        JOIN vic.dim_sales_rep AS r ON c.sales_rep_id = r.sales_rep_id
        WHERE c.client_id = ?
        """,
        [client_id],
    )
    return None if rows.empty else rows.iloc[0]


def client_monthly_notional(client_id: str) -> pd.DataFrame:
    """Return traded notional per month and asset class up to as_of."""
    return _query(
        """
        SELECT CAST(date_trunc('month', t.trade_date) AS DATE) AS month,
               CAST(p.asset_class AS VARCHAR) AS asset_class,
               CAST(sum(t.notional_eur) AS DOUBLE) AS notional_eur
        FROM vic.fact_trades AS t
        JOIN vic.dim_product AS p ON t.product_id = p.product_id
        CROSS JOIN vic.run_context AS rc
        WHERE t.client_id = ? AND t.trade_date <= rc.as_of
        GROUP BY ALL
        ORDER BY month, asset_class
        """,
        [client_id],
    )


def client_recent_trades(client_id: str, limit: int) -> pd.DataFrame:
    """Return the client's latest trades up to as_of."""
    return _query(
        """
        SELECT t.trade_date, p.name AS product_name,
               CAST(t.notional_eur AS DOUBLE) AS notional_eur
        FROM vic.fact_trades AS t
        JOIN vic.dim_product AS p ON t.product_id = p.product_id
        CROSS JOIN vic.run_context AS rc
        WHERE t.client_id = ? AND t.trade_date <= rc.as_of
        ORDER BY t.trade_date DESC, t.trade_id DESC
        LIMIT ?
        """,
        [client_id, limit],
    )


def client_open_positions(client_id: str) -> pd.DataFrame:
    """Return open positions ordered by maturity."""
    return _query(
        """
        SELECT p.position_id, pr.name AS product_name,
               CAST(p.notional_eur AS DOUBLE) AS notional_eur,
               p.maturity_date,
               date_diff('day', rc.as_of, p.maturity_date) AS days_to_maturity
        FROM vic.fact_positions AS p
        JOIN vic.dim_product AS pr ON p.product_id = pr.product_id
        CROSS JOIN vic.run_context AS rc
        WHERE p.client_id = ? AND p.start_date <= rc.as_of
            AND p.maturity_date > rc.as_of
        ORDER BY p.maturity_date
        """,
        [client_id],
    )


def client_impulses(client_id: str) -> pd.DataFrame:
    """Return the client's impulses of the latest run (rules and recommendations)."""
    return _query(
        """
        SELECT i.rank, p.name AS product_name, i.signal_type, i.score, i.reason_de
        FROM vic_app.daily_impulses AS i
        LEFT JOIN vic.dim_product AS p ON i.product_id = p.product_id
        WHERE i.client_id = ?
            AND i.run_date = (SELECT max(run_date) FROM vic_app.daily_impulses)
        ORDER BY i.score DESC
        """,
        [client_id],
    )


# --- Markt -------------------------------------------------------------------


def yield_curves() -> pd.DataFrame:
    """Return the curve at as_of and one month earlier (latest date on or before)."""
    return _query(
        """
        WITH points AS (
            SELECT 'aktuell' AS curve,
                   (SELECT max(rate_date) FROM vic.market_rates, vic.run_context
                    WHERE rate_date <= as_of) AS rate_date
            UNION ALL
            SELECT 'vor 1 Monat',
                   (SELECT max(rate_date) FROM vic.market_rates, vic.run_context
                    WHERE rate_date <= CAST(as_of - INTERVAL 1 MONTH AS DATE))
        )
        SELECT pt.curve, pt.rate_date, CAST(r.tenor_years AS DOUBLE) AS tenor_years,
               CAST(r.rate_pct AS DOUBLE) AS rate_pct
        FROM points AS pt
        JOIN vic.market_rates AS r ON r.rate_date = pt.rate_date
        ORDER BY pt.curve, r.tenor_years
        """
    )


def rate_changes() -> pd.DataFrame:
    """Return the bp changes per tenor from v_rate_changes."""
    return _query(
        """
        SELECT CAST(tenor_years AS DOUBLE) AS tenor_years, rate_date,
               CAST(rate_pct AS DOUBLE) AS rate_pct,
               CAST(change_14d_bp AS DOUBLE) AS change_14d_bp,
               CAST(change_1m_bp AS DOUBLE) AS change_1m_bp
        FROM vic.v_rate_changes
        ORDER BY tenor_years
        """
    )


# --- Monitoring ----------------------------------------------------------------


def acceptance_by_week() -> pd.DataFrame:
    """Return the acceptance rate per ISO week and signal type (latest status)."""
    sql = (
        "WITH"
        + LATEST_FEEDBACK_CTE
        + """
SELECT CAST(date_trunc('week', i.run_date) AS DATE) AS week, i.signal_type,
       count(*) FILTER (WHERE f.status = 'angenommen') AS accepted,
       count(*) AS decided
FROM latest_feedback AS f
JOIN vic_app.daily_impulses AS i ON f.impulse_id = i.impulse_id
GROUP BY ALL
ORDER BY week, i.signal_type
"""
    )
    rates = _query(sql)
    rates["acceptance_rate"] = rates["accepted"] / rates["decided"]
    return rates


def reco_eval_runs(ground_truth: str, k: int) -> pd.DataFrame:
    """Return the recommender metrics of every evaluation run."""
    return _query(
        """
        SELECT run_ts, model, precision_at_k, recall_at_k, ndcg_at_k, coverage
        FROM vic.reco_eval_runs
        WHERE ground_truth = ? AND k = ?
        ORDER BY run_ts, model
        """,
        [ground_truth, k],
    )


def latest_quality_results() -> pd.DataFrame:
    """Return the check results of the latest pipeline run, failures first."""
    return _query(
        """
        SELECT run_ts, run_date, dataset, check_name, passed, failures
        FROM vic_app.quality_results
        WHERE run_ts = (SELECT max(run_ts) FROM vic_app.quality_results)
        ORDER BY passed, dataset, check_name
        """
    )


def client_options() -> pd.DataFrame:
    """Return all clients (id, name, segment) for the client picker."""
    return _query(
        """
        SELECT client_id, name, CAST(segment AS VARCHAR) AS segment
        FROM vic.dim_client
        ORDER BY name
        """
    )
