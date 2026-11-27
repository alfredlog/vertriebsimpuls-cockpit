"""Data quality checks with pandera, summarised as one row per check.

Every check carries an explicit name, so the summary lists passed checks too
(not only failures) and the monitoring page can show a stable list.
"""

from datetime import date, datetime

import duckdb
import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

from vic.data.generate import RISK_PROFILES, SEGMENT_SHARE
from vic.db import insert_frame
from vic.ranking.score import SIGNAL_TYPE_RECOMMENDATION, TOP_N

SEGMENTS = tuple(SEGMENT_SHARE)
SIGNAL_TYPES = (
    "maturity",
    "rate_move",
    "reactivation",
    "concentration",
    SIGNAL_TYPE_RECOMMENDATION,
)
SUMMARY_COLUMNS = ["dataset", "check", "passed", "failures"]

# pandera's names for its built-in column properties -> our labels.
BUILTIN_CHECK_LABELS = {
    "field_uniqueness": "unique",
    "not_nullable": "not_null",
}


class QualityCheckError(Exception):
    """Raised when at least one data quality check fails."""


def _positive(name: str = "positive") -> pa.Check:
    return pa.Check.gt(0, name=name)


def clients_schema() -> pa.DataFrameSchema:
    """Clients: unique ids, known segments and risk profiles, positive AuM."""
    return pa.DataFrameSchema(
        {
            "client_id": pa.Column(str, unique=True),
            "segment": pa.Column(str, pa.Check.isin(SEGMENTS, name="allowed_values")),
            "risk_profile": pa.Column(
                str, pa.Check.isin(RISK_PROFILES, name="allowed_values")
            ),
            "aum_eur": pa.Column(float, _positive()),
            "has_floating_rate_debt": pa.Column(bool),
            "sales_rep_id": pa.Column(str),
        }
    )


def trades_schema(as_of: date) -> pa.DataFrameSchema:
    """Trades: unique ids, positive notional, no trade after as_of."""
    as_of_ts = pd.Timestamp(as_of)
    return pa.DataFrameSchema(
        {
            "trade_id": pa.Column(str, unique=True),
            "trade_date": pa.Column(
                pa.DateTime, pa.Check.le(as_of_ts, name="not_after_as_of")
            ),
            "client_id": pa.Column(str),
            "product_id": pa.Column(str),
            "notional_eur": pa.Column(float, _positive()),
        }
    )


def positions_schema() -> pa.DataFrameSchema:
    """Positions: unique ids, positive notional, maturity after start."""
    return pa.DataFrameSchema(
        {
            "position_id": pa.Column(str, unique=True),
            "client_id": pa.Column(str),
            "product_id": pa.Column(str),
            "notional_eur": pa.Column(float, _positive()),
            "start_date": pa.Column(pa.DateTime),
            "maturity_date": pa.Column(pa.DateTime),
        },
        checks=[
            pa.Check(
                lambda df: df["maturity_date"] > df["start_date"],
                name="maturity_after_start",
            )
        ],
    )


def impulses_schema(top_n: int = TOP_N) -> pa.DataFrameSchema:
    """Daily impulses: unique ids, known signal types, ranks 1..top_n per rep."""
    return pa.DataFrameSchema(
        {
            "impulse_id": pa.Column(str, unique=True),
            "run_date": pa.Column(nullable=False),
            "sales_rep_id": pa.Column(str),
            "client_id": pa.Column(str),
            "product_id": pa.Column(str, nullable=True),
            "signal_type": pa.Column(
                str, pa.Check.isin(SIGNAL_TYPES, name="allowed_values")
            ),
            "score": pa.Column(float, pa.Check.ge(0, name="not_negative")),
            "rank": pa.Column(int, pa.Check.in_range(1, top_n, name="in_range")),
            "reason_de": pa.Column(
                str, pa.Check.str_length(min_value=1, name="not_empty")
            ),
        },
        unique=["run_date", "sales_rep_id", "rank"],
    )


def _expected_labels(schema: pa.DataFrameSchema) -> list[str]:
    """List every check the schema runs, as 'column.check' or 'check'."""
    labels = []
    for name, column in schema.columns.items():
        labels.append(f"{name}.present")
        if column.dtype is not None:
            labels.append(f"{name}.dtype")
        if column.unique:
            labels.append(f"{name}.unique")
        if not column.nullable:
            labels.append(f"{name}.not_null")
        labels += [f"{name}.{check.name}" for check in column.checks]
    labels += [check.name for check in schema.checks]
    if schema.unique:
        labels.append("unique_rows")
    return labels


def _check_names(schema: pa.DataFrameSchema) -> dict[tuple, str]:
    """Map (column, identifier pandera reports on failure) -> our check name.

    Built-in checks report their error text (e.g. 'greater_than(0)') instead
    of the name we gave them; custom checks report their name.
    """
    names = {}
    for column_name, column in schema.columns.items():
        for check in column.checks:
            names[(column_name, check.error or check.name)] = check.name
    for check in schema.checks:
        names[(None, check.error or check.name)] = check.name
    return names


def _failure_label(row: pd.Series, check_names: dict[tuple, str]) -> str:
    """Map one pandera failure case to the label used in _expected_labels."""
    check = str(row["check"])
    if check == "column_in_dataframe":
        return f"{row['failure_case']}.present"
    if check == "multiple_fields_uniqueness":
        return "unique_rows"
    if row["schema_context"] != "Column":
        # DataFrame-wide checks report one failure per column; use the check only.
        return check_names.get((None, check), check)
    column = row["column"]
    if check.startswith("dtype("):
        return f"{column}.dtype"
    if check in BUILTIN_CHECK_LABELS:
        return f"{column}.{BUILTIN_CHECK_LABELS[check]}"
    return f"{column}.{check_names.get((column, check), check)}"


def check_dataset(
    name: str, frame: pd.DataFrame, schema: pa.DataFrameSchema
) -> pd.DataFrame:
    """Validate one DataFrame; return one summary row per check."""
    failures: dict[str, int] = {}
    try:
        schema.validate(frame, lazy=True)
    except SchemaErrors as errors:
        cases = errors.failure_cases
        check_names = _check_names(schema)
        cases = cases.assign(
            label=[_failure_label(row, check_names) for _, row in cases.iterrows()]
        )
        # Count failing rows, not failing cells; schema-level errors have no index.
        failures = cases.groupby("label")["index"].nunique(dropna=False).to_dict()

    labels = _expected_labels(schema)
    labels += [label for label in failures if label not in labels]
    rows = [
        {
            "dataset": name,
            "check": label,
            "passed": failures.get(label, 0) == 0,
            "failures": int(failures.get(label, 0)),
        }
        for label in labels
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def check_input_data(data: dict[str, pd.DataFrame], as_of: date) -> pd.DataFrame:
    """Run the checks on the generator output (clients, trades, positions)."""
    summaries = [
        check_dataset("clients", data["clients"], clients_schema()),
        check_dataset("trades", data["trades"], trades_schema(as_of)),
        check_dataset("positions", data["positions"], positions_schema()),
    ]
    return pd.concat(summaries, ignore_index=True)


def store_quality_results(
    con: duckdb.DuckDBPyConnection,
    summary: pd.DataFrame,
    run_ts: datetime,
    run_date: date,
) -> None:
    """Append a check summary to vic_app.quality_results."""
    results = summary.rename(columns={"check": "check_name"})
    results.insert(0, "run_ts", run_ts)
    results.insert(1, "run_date", run_date)
    insert_frame(con, "quality_results", results, schema="vic_app")


def raise_on_failure(summary: pd.DataFrame) -> None:
    """Raise QualityCheckError listing the failed checks, if any."""
    failed = summary[~summary["passed"]]
    if not failed.empty:
        details = ", ".join(
            f"{row.dataset}.{row.check} ({row.failures})" for row in failed.itertuples()
        )
        raise QualityCheckError(f"Data quality checks failed: {details}")
