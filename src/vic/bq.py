"""Optional BigQuery backend: load the star schema with load jobs, run dry runs.

Install with `uv sync --group bigquery`; authenticate with Application
Default Credentials (`gcloud auth application-default login`), no key files.

Sandbox constraints this module is built around (BigQuery sandbox docs):
- no DML and no streaming, so every table is (re)written by a load job with
  WRITE_TRUNCATE; a reload replaces each table, it never updates rows;
- tables and partitions expire after 60 days, and a time-unit column
  partition expires relative to its partition date: partitions older than
  60 days are expired immediately when loaded. Our trades reach back two
  years, so in sandbox mode fact_trades is NOT partitioned (it would lose
  almost all rows); it is clustered by trade_date, client_id instead;
- 10 GiB lifetime storage that is not refunded on deletion, so reloads
  should stay rare (the whole demo dataset is a few MB).

CLI: uv run --group bigquery python -m vic.bq {load,dry-run,estimate} --help
"""

import argparse
import importlib.resources
import logging
import math
from datetime import date
from decimal import Decimal

import pandas as pd
from google.cloud import bigquery

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import DEFAULT_SEED, TABLE_TO_DATA_KEY, dim_date_for_trades

logger = logging.getLogger(__name__)

DATASET = "vic"
LOCATION = "EU"
MONEY_DECIMALS = 2
MB = 1024 * 1024
# On-demand minimum: 10 MB per referenced table and 10 MB per query.
MIN_BILLED_BYTES_PER_TABLE = 10 * MB
TRADE_PARTITION_FIELD = "trade_date"
TRADE_CLUSTER_FIELDS = ["client_id"]
# Sandbox substitute for partitioning: date-sorted blocks still allow pruning.
SANDBOX_TRADE_CLUSTER_FIELDS = ["trade_date", "client_id"]
HISTORY_MONTHS_IN_QUERIES = 12

# DuckDB -> BigQuery: DECIMAL -> NUMERIC, ENUM -> STRING, INTEGER -> INT64.
SCHEMAS = {
    "dim_sales_rep": [
        ("sales_rep_id", "STRING", "REQUIRED"),
        ("name", "STRING", "REQUIRED"),
        ("region", "STRING", "REQUIRED"),
    ],
    "dim_product": [
        ("product_id", "STRING", "REQUIRED"),
        ("name", "STRING", "REQUIRED"),
        ("asset_class", "STRING", "REQUIRED"),
        ("tenor_years", "NUMERIC", "NULLABLE"),
        ("risk_level", "INT64", "REQUIRED"),
        ("is_hedging_product", "BOOL", "REQUIRED"),
    ],
    "dim_client": [
        ("client_id", "STRING", "REQUIRED"),
        ("name", "STRING", "REQUIRED"),
        ("segment", "STRING", "REQUIRED"),
        ("region", "STRING", "REQUIRED"),
        ("aum_eur", "NUMERIC", "REQUIRED"),
        ("risk_profile", "STRING", "REQUIRED"),
        ("has_floating_rate_debt", "BOOL", "REQUIRED"),
        ("sales_rep_id", "STRING", "REQUIRED"),
    ],
    "dim_date": [
        ("date", "DATE", "REQUIRED"),
        ("quarter", "INT64", "REQUIRED"),
        ("month", "INT64", "REQUIRED"),
        ("year", "INT64", "REQUIRED"),
        ("month_name", "STRING", "REQUIRED"),
        ("iso_week", "INT64", "REQUIRED"),
        ("weekday", "STRING", "REQUIRED"),
        ("is_weekend", "BOOL", "REQUIRED"),
        ("is_month_end", "BOOL", "REQUIRED"),
    ],
    "fact_trades": [
        ("trade_id", "STRING", "REQUIRED"),
        ("trade_date", "DATE", "REQUIRED"),
        ("client_id", "STRING", "REQUIRED"),
        ("product_id", "STRING", "REQUIRED"),
        ("notional_eur", "NUMERIC", "REQUIRED"),
    ],
    "fact_positions": [
        ("position_id", "STRING", "REQUIRED"),
        ("client_id", "STRING", "REQUIRED"),
        ("product_id", "STRING", "REQUIRED"),
        ("notional_eur", "NUMERIC", "REQUIRED"),
        ("start_date", "DATE", "REQUIRED"),
        ("maturity_date", "DATE", "REQUIRED"),
    ],
}

# Logical bytes per value (BigQuery "Data type sizes"); STRING adds UTF-8 length.
TYPE_BYTES = {"INT64": 8, "DATE": 8, "FLOAT64": 8, "NUMERIC": 16, "BOOL": 1}
STRING_OVERHEAD_BYTES = 2

# Example queries and the columns each reads; a test keeps this in sync with
# the SQL files. True = the table is pruned to the 12-month partitions.
QUERY_FOOTPRINTS = {
    "volume_per_segment_month": [
        ("fact_trades", ["trade_date", "client_id", "notional_eur"], True),
        ("dim_client", ["client_id", "segment"], False),
    ],
    "upcoming_maturities": [
        (
            "fact_positions",
            [
                "position_id",
                "client_id",
                "product_id",
                "notional_eur",
                "start_date",
                "maturity_date",
            ],
            False,
        ),
        ("dim_client", ["client_id", "name"], False),
        ("dim_product", ["product_id", "name"], False),
    ],
    "top_products_per_segment": [
        (
            "fact_trades",
            ["trade_date", "client_id", "product_id", "notional_eur"],
            True,
        ),
        ("dim_client", ["client_id", "segment"], False),
        ("dim_product", ["product_id", "name"], False),
    ],
}


# --- Preparing frames ---------------------------------------------------------


def star_schema_frames(data: dict[str, pd.DataFrame], as_of: date) -> dict:
    """Return generator output plus dim_date, keyed by table name."""
    frames = {table: data[key] for table, key in TABLE_TO_DATA_KEY.items()}
    frames["dim_date"] = dim_date_for_trades(data["trades"], as_of)
    return frames


def _to_decimal(value: float) -> Decimal | None:
    """NUMERIC needs Decimal values; the client rejects floats for NUMERIC."""
    if value is None or pd.isna(value):
        return None
    return Decimal(str(round(float(value), MONEY_DECIMALS)))


def to_bigquery_frame(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Select the schema columns in order and convert to load-job types."""
    out = pd.DataFrame(index=frame.index)
    for name, bq_type, _mode in SCHEMAS[table]:
        column = frame[name]
        if bq_type == "DATE":
            out[name] = pd.to_datetime(column).dt.date
        elif bq_type == "NUMERIC":
            out[name] = column.map(_to_decimal).astype(object)
        elif bq_type == "INT64":
            out[name] = column.astype("int64")
        elif bq_type == "BOOL":
            out[name] = column.astype(bool)
        else:
            out[name] = column.astype(str)
    return out.reset_index(drop=True)


# --- Loading ------------------------------------------------------------------


def schema_fields(table: str) -> list[bigquery.SchemaField]:
    """Return the BigQuery schema of a table."""
    return [
        bigquery.SchemaField(name, bq_type, mode=mode)
        for name, bq_type, mode in SCHEMAS[table]
    ]


def load_job_config(table: str, sandbox: bool) -> bigquery.LoadJobConfig:
    """Load job settings; fact_trades gets partitioning/clustering per mode."""
    config = bigquery.LoadJobConfig(
        schema=schema_fields(table),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    if table != "fact_trades":
        return config
    if sandbox:
        config.clustering_fields = SANDBOX_TRADE_CLUSTER_FIELDS
    else:
        # expiration_ms=None: historical partitions must never expire.
        config.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.MONTH,
            field=TRADE_PARTITION_FIELD,
            expiration_ms=None,
        )
        config.clustering_fields = TRADE_CLUSTER_FIELDS
    return config


def ensure_dataset(client: bigquery.Client) -> None:
    """Create dataset 'vic' if missing (the sandbox adds its 60-day expiry)."""
    dataset = bigquery.Dataset(f"{client.project}.{DATASET}")
    dataset.location = LOCATION
    client.create_dataset(dataset, exists_ok=True)


def load_star_schema(
    client: bigquery.Client,
    data: dict[str, pd.DataFrame],
    as_of: date,
    sandbox: bool,
) -> dict[str, int]:
    """Load every star schema table with a WRITE_TRUNCATE load job.

    Tables are loaded one by one; there is no cross-table transaction without
    DML, so a failed run can leave tables from two different runs. A rerun
    fixes that, because every load fully replaces its table.
    """
    ensure_dataset(client)
    rows = {}
    for table, frame in star_schema_frames(data, as_of).items():
        table_id = f"{client.project}.{DATASET}.{table}"
        job = client.load_table_from_dataframe(
            to_bigquery_frame(table, frame),
            table_id,
            job_config=load_job_config(table, sandbox),
        )
        job.result()
        rows[table] = len(frame)
        logger.info("loaded %s (%d rows)", table_id, len(frame))
    return rows


# --- Queries and cost ---------------------------------------------------------


def read_query(name: str) -> str:
    """Return the SQL of an example query from sql/bigquery/."""
    folder = importlib.resources.files("vic.sql").joinpath("bigquery")
    return folder.joinpath(f"{name}.sql").read_text(encoding="utf-8")


def dry_run_bytes(client: bigquery.Client, name: str, as_of: date) -> int:
    """Return total_bytes_processed of a dry run (free, nothing is executed)."""
    config = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False,
        query_parameters=[bigquery.ScalarQueryParameter("as_of", "DATE", as_of)],
    )
    job = client.query(read_query(name), job_config=config)
    return job.total_bytes_processed


def column_bytes(frame: pd.DataFrame, table: str, columns: list[str]) -> int:
    """Logical bytes of the given columns, by BigQuery's data type sizes."""
    types = {name: bq_type for name, bq_type, _mode in SCHEMAS[table]}
    total = 0
    for name in columns:
        values = frame[name].dropna()  # NULL values count as 0 bytes
        if types[name] == "STRING":
            lengths = values.astype(str).map(lambda v: len(v.encode("utf-8")))
            total += int((lengths + STRING_OVERHEAD_BYTES).sum())
        else:
            total += TYPE_BYTES[types[name]] * len(values)
    return total


def _in_scanned_partitions(trades: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Rows in the monthly partitions a 12-month filter has to read."""
    window_start = pd.Timestamp(as_of) - pd.DateOffset(months=HISTORY_MONTHS_IN_QUERIES)
    first_partition = window_start.to_period("M").start_time
    trade_dates = pd.to_datetime(trades["trade_date"])
    return trades[
        (trade_dates >= first_partition) & (trade_dates <= pd.Timestamp(as_of))
    ]


def estimate_query_bytes(data: dict[str, pd.DataFrame], as_of: date) -> pd.DataFrame:
    """Estimate bytes per example query: SELECT *, used columns, plus pruning.

    A calculation from the data type sizes, not a measurement; use
    dry_run_bytes for the real number. Billed bytes are at least 10 MB per
    referenced table, which dominates for a dataset this small.
    """
    frames = star_schema_frames(data, as_of)
    rows = []
    for query, footprint in QUERY_FOOTPRINTS.items():
        all_columns = used_columns = pruned = 0
        for table, columns, prunable in footprint:
            frame = frames[table]
            every_column = [name for name, _type, _mode in SCHEMAS[table]]
            all_columns += column_bytes(frame, table, every_column)
            used = column_bytes(frame, table, columns)
            used_columns += used
            if prunable:
                pruned += column_bytes(
                    _in_scanned_partitions(frame, as_of), table, columns
                )
            else:
                pruned += used
        rows.append(
            {
                "query": query,
                "tables": len(footprint),
                "bytes_select_star": all_columns,
                "bytes_used_columns": used_columns,
                "bytes_with_partition_pruning": pruned,
                "min_billed_bytes": MIN_BILLED_BYTES_PER_TABLE * len(footprint),
            }
        )
    return pd.DataFrame(rows)


# --- CLI ----------------------------------------------------------------------


def _format_kb(value: int) -> str:
    return f"{math.ceil(value / 1024):,} KB".replace(",", ".")


def main(argv: list[str] | None = None) -> None:
    """CLI: load, dry-run or estimate."""
    parser = argparse.ArgumentParser(description="BigQuery backend for vic.")
    parser.add_argument("command", choices=["load", "dry-run", "estimate"])
    parser.add_argument("--project", help="GCP project (default: from ADC)")
    parser.add_argument("--as-of", type=date.fromisoformat, default=AS_OF_DEFAULT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="BigQuery sandbox: no partitioning of fact_trades (60-day expiry)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "estimate":
        data = generate_all(seed=args.seed, as_of=args.as_of)
        estimate = estimate_query_bytes(data, args.as_of)
        for column in estimate.columns[2:]:
            estimate[column] = estimate[column].map(_format_kb)
        logger.info("estimated bytes per query:\n%s", estimate.to_string(index=False))
        return

    client = bigquery.Client(project=args.project)
    if args.command == "load":
        data = generate_all(seed=args.seed, as_of=args.as_of)
        load_star_schema(client, data, args.as_of, args.sandbox)
    else:
        for name in QUERY_FOOTPRINTS:
            processed = dry_run_bytes(client, name, args.as_of)
            logger.info("%s: %s processed (dry run)", name, _format_kb(processed))


if __name__ == "__main__":
    main()
