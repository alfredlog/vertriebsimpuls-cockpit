"""BigQuery loader with a mocked client; no network, no credentials.

Deselected in the default run (marker). vic.bq is imported inside the tests,
so collecting this file never imports BigQuery code.
Run: uv run --group bigquery pytest -m bigquery
"""

import re
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all

pytestmark = pytest.mark.bigquery


def test_load_star_schema_with_mocked_client():
    pytest.importorskip("google.cloud.bigquery")
    from google.cloud import bigquery

    from vic import bq

    client = MagicMock()
    client.project = "demo-project"
    data = generate_all(seed=42, as_of=AS_OF_DEFAULT)

    rows = bq.load_star_schema(client, data, AS_OF_DEFAULT, sandbox=False)

    client.create_dataset.assert_called_once()
    calls = {
        call.args[1]: call for call in client.load_table_from_dataframe.call_args_list
    }
    assert set(calls) == {f"demo-project.vic.{table}" for table in bq.SCHEMAS}
    assert rows["fact_trades"] == len(data["trades"])

    for call in calls.values():
        config = call.kwargs["job_config"]
        assert config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE

    trades_call = calls["demo-project.vic.fact_trades"]
    config = trades_call.kwargs["job_config"]
    assert config.time_partitioning.type_ == bigquery.TimePartitioningType.MONTH
    assert config.time_partitioning.field == "trade_date"
    assert config.time_partitioning.expiration_ms is None
    assert config.clustering_fields == ["client_id"]

    frame = trades_call.args[0]
    assert list(frame.columns) == [name for name, _t, _m in bq.SCHEMAS["fact_trades"]]
    assert isinstance(frame.loc[0, "notional_eur"], Decimal)
    assert frame.loc[0, "trade_date"] == data["trades"].loc[0, "trade_date"].date()


def test_sandbox_mode_does_not_partition_trades():
    pytest.importorskip("google.cloud.bigquery")
    from vic import bq

    config = bq.load_job_config("fact_trades", sandbox=True)

    # Partitions older than 60 days would expire on load in the sandbox.
    assert config.time_partitioning is None
    assert config.clustering_fields == ["trade_date", "client_id"]


def test_query_footprints_match_sql_files():
    pytest.importorskip("google.cloud.bigquery")
    from vic import bq

    for query, footprint in bq.QUERY_FOOTPRINTS.items():
        sql = bq.read_query(query)
        assert "@as_of" in sql
        for table, columns, _prunable in footprint:
            assert f"vic.{table}" in sql, (query, table)
            for column in columns:
                assert re.search(rf"\.{column}\b", sql), (query, column)


def test_estimate_shows_savings_from_columns_and_pruning():
    pytest.importorskip("google.cloud.bigquery")
    from vic import bq

    data = generate_all(seed=42, as_of=AS_OF_DEFAULT)
    estimate = bq.estimate_query_bytes(data, AS_OF_DEFAULT).set_index("query")

    assert (estimate["bytes_used_columns"] <= estimate["bytes_select_star"]).all()
    pruned = estimate.loc["volume_per_segment_month"]
    assert pruned["bytes_with_partition_pruning"] < pruned["bytes_used_columns"]
    assert estimate.loc["upcoming_maturities", "min_billed_bytes"] == 3 * 10 * 1024**2
