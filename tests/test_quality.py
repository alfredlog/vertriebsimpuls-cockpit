from datetime import date, datetime

import pandas as pd
import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import ensure_app_schema, get_connection
from vic.quality.checks import (
    SUMMARY_COLUMNS,
    QualityCheckError,
    check_dataset,
    check_input_data,
    clients_schema,
    impulses_schema,
    positions_schema,
    raise_on_failure,
    store_quality_results,
    trades_schema,
)


@pytest.fixture(scope="module")
def data():
    return generate_all(seed=42, as_of=AS_OF_DEFAULT)


def failed(summary: pd.DataFrame) -> dict[str, int]:
    rows = summary[~summary["passed"]]
    return dict(zip(rows["check"], rows["failures"], strict=True))


def test_generated_data_passes_all_checks(data):
    summary = check_input_data(data, AS_OF_DEFAULT)

    assert list(summary.columns) == SUMMARY_COLUMNS
    assert summary["passed"].all()
    assert set(summary["dataset"]) == {"clients", "trades", "positions"}
    assert not summary.duplicated(["dataset", "check"]).any()


def test_broken_clients_fail_with_counts(data):
    clients = data["clients"].head(5).copy()
    clients.loc[0, "segment"] = "retail"
    clients.loc[1, "client_id"] = clients.loc[2, "client_id"]
    clients.loc[3, "aum_eur"] = -1.0

    summary = check_dataset("clients", clients, clients_schema())

    assert failed(summary) == {
        "segment.allowed_values": 1,
        "client_id.unique": 2,
        "aum_eur.positive": 1,
    }
    with pytest.raises(QualityCheckError, match="clients.segment.allowed_values"):
        raise_on_failure(summary)


def test_future_trade_date_fails(data):
    trades = data["trades"].head(3).copy()
    trades.loc[0, "trade_date"] = pd.Timestamp("2026-11-21")  # as_of + 1

    summary = check_dataset("trades", trades, trades_schema(AS_OF_DEFAULT))
    assert failed(summary) == {"trade_date.not_after_as_of": 1}


def test_trade_on_as_of_passes(data):
    trades = data["trades"].head(3).copy()
    trades.loc[0, "trade_date"] = pd.Timestamp(AS_OF_DEFAULT)

    summary = check_dataset("trades", trades, trades_schema(AS_OF_DEFAULT))
    assert summary["passed"].all()


def test_maturity_not_after_start_fails(data):
    positions = data["positions"].head(3).copy()
    positions.loc[0, "maturity_date"] = positions.loc[0, "start_date"]

    summary = check_dataset("positions", positions, positions_schema())
    # Counted once per row, although pandera reports it for every column.
    assert failed(summary) == {"maturity_after_start": 1}


def test_missing_column_fails(data):
    clients = data["clients"].drop(columns="risk_profile")
    summary = check_dataset("clients", clients, clients_schema())
    assert failed(summary) == {"risk_profile.present": 1}


def test_impulses_rank_and_uniqueness():
    impulses = pd.DataFrame(
        {
            "impulse_id": ["a", "b"],
            "run_date": [date(2026, 11, 20)] * 2,
            "sales_rep_id": ["R1", "R1"],
            "client_id": ["C1", "C2"],
            "product_id": ["P1", None],
            "signal_type": ["maturity", "unknown"],
            "score": [0.9, 0.5],
            "rank": [1, 1],  # duplicate rank for the same rep and day
            "reason_de": ["Grund", "Grund"],
        }
    )
    summary = check_dataset("daily_impulses", impulses, impulses_schema())
    assert failed(summary) == {"signal_type.allowed_values": 1, "unique_rows": 2}


def test_results_are_stored(data):
    con = get_connection(":memory:")
    ensure_app_schema(con)
    summary = check_input_data(data, AS_OF_DEFAULT)
    store_quality_results(con, summary, datetime(2026, 11, 20, 6), AS_OF_DEFAULT)

    stored = con.execute("SELECT count(*) FROM vic_app.quality_results").fetchone()
    assert stored == (len(summary),)
