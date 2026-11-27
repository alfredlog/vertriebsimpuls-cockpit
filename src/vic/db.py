"""DuckDB access layer: the single entry point to the database."""

import importlib.resources
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from vic.data.generate import AS_OF_DEFAULT, generate_all

DEFAULT_DB_PATH = Path("data/vic.duckdb")
DEFAULT_SEED = 42
FILE_CATALOG = "vic_db"

TABLE_LOAD_ORDER = (
    "dim_sales_rep",
    "dim_product",
    "dim_date",
    "dim_client",
    "fact_trades",
    "fact_positions",
)

# dim_date is derived from the trades, so it has no generator key.
TABLE_TO_DATA_KEY = {
    "dim_sales_rep": "sales_reps",
    "dim_product": "products",
    "dim_client": "clients",
    "fact_trades": "trades",
    "fact_positions": "positions",
}


def get_connection(path: str | Path = DEFAULT_DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection; creates the parent folder for file databases.

    DuckDB names a file's catalog after the file, so "vic.duckdb" would clash
    with the schema "vic". Attaching under a fixed alias avoids that ambiguity.
    """
    if path == ":memory:":
        return duckdb.connect(path)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    escaped = str(path).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS {FILE_CATALOG}")
    con.execute(f"USE {FILE_CATALOG}")
    return con


def query_df(
    con: duckdb.DuckDBPyConnection, sql: str, params: list | dict | None = None
) -> pd.DataFrame:
    """Run a (parameterised) query and return the result as a DataFrame."""
    return con.execute(sql, params).df()


def build_dim_date(start: date, end: date) -> pd.DataFrame:
    """Return one calendar row per day from start to end (both inclusive)."""
    if start > end:
        raise ValueError("Start date must not be after end date")

    dates = pd.date_range(start, end)
    dim_date = pd.DataFrame({"date": dates})
    dim_date["quarter"] = dim_date["date"].dt.quarter
    dim_date["month"] = dim_date["date"].dt.month
    dim_date["year"] = dim_date["date"].dt.year
    dim_date["month_name"] = dim_date["date"].dt.month_name()
    dim_date["iso_week"] = dim_date["date"].dt.isocalendar().week
    dim_date["weekday"] = dim_date["date"].dt.day_name()
    dim_date["is_weekend"] = dim_date["date"].dt.day_of_week >= 5
    dim_date["is_month_end"] = dim_date["date"].dt.is_month_end
    dim_date["date"] = dim_date["date"].dt.date
    return dim_date


def _dim_date_for_trades(trades: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Build dim_date covering every trade date and as_of."""
    trade_dates = pd.to_datetime(trades["trade_date"])
    if trade_dates.isna().any():
        raise ValueError("Trade dates must not contain missing values")
    if trade_dates.empty:
        return build_dim_date(as_of, as_of)
    start = min(trade_dates.min().date(), as_of)
    end = max(trade_dates.max().date(), as_of)
    return build_dim_date(start, end)


def _insert_frame(
    con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame
) -> None:
    """Insert a DataFrame into vic.<table>, matching columns by name."""
    con.register("df", frame)
    try:
        con.execute(f"INSERT INTO vic.{table} BY NAME SELECT * FROM df")
    finally:
        con.unregister("df")


def load_all(
    con: duckdb.DuckDBPyConnection, data: dict[str, pd.DataFrame], as_of: date
) -> None:
    """Replace the whole vic schema with the given data in one transaction.

    DuckDB cannot delete rows that are referenced by a foreign key within the
    same transaction, so a reload drops the schema (CASCADE), recreates it from
    schema.sql and inserts everything. Doing all of it in one transaction means
    a failed load rolls back to the previous data instead of an empty schema.
    """
    expected_keys = set(TABLE_TO_DATA_KEY.values())
    missing = expected_keys - set(data)
    unexpected = set(data) - expected_keys
    if missing or unexpected:
        raise ValueError(f"Missing keys: {missing}, unexpected keys: {unexpected}")

    frames = {table: data[key] for table, key in TABLE_TO_DATA_KEY.items()}
    frames["dim_date"] = _dim_date_for_trades(data["trades"], as_of)
    schema_sql = importlib.resources.files("vic.sql").joinpath("schema.sql").read_text()

    con.begin()
    try:
        con.execute("DROP SCHEMA IF EXISTS vic CASCADE;")
        con.execute(schema_sql)
        for table in TABLE_LOAD_ORDER:
            _insert_frame(con, table, frames[table])
        con.commit()
    except Exception:
        con.rollback()
        raise


def build_database(
    path: str | Path = DEFAULT_DB_PATH,
    seed: int = DEFAULT_SEED,
    as_of: date = AS_OF_DEFAULT,
) -> None:
    """Generate the synthetic dataset and load it into the database at path."""
    data = generate_all(seed=seed, as_of=as_of)
    with get_connection(path) as con:
        load_all(con, data, as_of)


if __name__ == "__main__":
    build_database()
    print(f"Database built at {DEFAULT_DB_PATH}")
