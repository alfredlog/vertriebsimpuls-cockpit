import subprocess
import sys
import time
from datetime import date

import duckdb
import pandas as pd
import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import (
    TABLE_LOAD_ORDER,
    TABLE_TO_DATA_KEY,
    build_database,
    build_dim_date,
    get_connection,
    load_all,
    query_df,
)

AS_OF = date(2026, 3, 2)


def small_data() -> dict[str, pd.DataFrame]:
    """Hand-made dataset: 1 rep, 2 products, 2 clients, 3 trades, 1 position."""
    return {
        "sales_reps": pd.DataFrame(
            {"sales_rep_id": ["R01"], "name": ["Anna Becker"], "region": ["Nord"]}
        ),
        "products": pd.DataFrame(
            {
                "product_id": ["P001", "P015"],
                "name": ["Bundesanleihe 10J", "Renten-ETF"],
                "asset_class": ["government_bond", "fund"],
                "tenor_years": [10.0, None],
                "risk_level": [1, 3],
                "is_hedging_product": [False, False],
            }
        ),
        "clients": pd.DataFrame(
            {
                "client_id": ["C0001", "C0002"],
                "name": ["Volksbank Birnfeld", "Energie Tannhagen"],
                "segment": ["cooperative_bank", "corporate"],
                "region": ["Nord", "Nord"],
                "aum_eur": [800_000_000.0, 150_000_000.0],
                "risk_profile": ["conservative", "balanced"],
                "has_floating_rate_debt": [False, True],
                "sales_rep_id": ["R01", "R01"],
            }
        ),
        "trades": pd.DataFrame(
            {
                "trade_id": ["T000001", "T000002", "T000003"],
                "trade_date": pd.to_datetime(
                    ["2026-02-27", "2026-02-28", "2026-03-01"]
                ),
                "client_id": ["C0001", "C0002", "C0001"],
                "product_id": ["P001", "P015", "P015"],
                "notional_eur": [5_000_000.0, 1_000_000.0, 2_000_000.0],
            }
        ),
        "positions": pd.DataFrame(
            {
                "position_id": ["POS000001"],
                "client_id": ["C0001"],
                "product_id": ["P001"],
                "notional_eur": [5_000_000.0],
                "start_date": pd.to_datetime(["2026-02-27"]),
                "maturity_date": pd.to_datetime(["2036-02-27"]),
            }
        ),
    }


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        table: con.execute(f"SELECT count(*) FROM vic.{table}").fetchone()[0]
        for table in TABLE_LOAD_ORDER
    }


@pytest.fixture
def con():
    connection = get_connection(":memory:")
    yield connection
    connection.close()


def test_row_counts_match_dataframes(con):
    data = small_data()
    load_all(con, data, AS_OF)

    counts = row_counts(con)
    for table, key in TABLE_TO_DATA_KEY.items():
        assert counts[table] == len(data[key])
    # 2026-02-27 .. 2026-03-02 inclusive
    assert counts["dim_date"] == 4


def test_reload_is_idempotent(con):
    load_all(con, small_data(), AS_OF)
    first = row_counts(con)
    load_all(con, small_data(), AS_OF)
    assert row_counts(con) == first


def test_dim_date_covers_trades_and_as_of(con):
    load_all(con, small_data(), AS_OF)

    missing = con.execute(
        """
        SELECT count(*) FROM vic.fact_trades t
        LEFT JOIN vic.dim_date d ON t.trade_date = d.date
        WHERE d.date IS NULL
        """
    ).fetchone()[0]
    assert missing == 0
    assert con.execute(
        "SELECT count(*) FROM vic.dim_date WHERE date = ?", [AS_OF]
    ).fetchone() == (1,)


def test_invalid_segment_rolls_back_to_previous_data(con):
    load_all(con, small_data(), AS_OF)
    before = row_counts(con)

    bad = small_data()
    bad["clients"].loc[0, "segment"] = "retail"
    bad["trades"] = bad["trades"].iloc[:1]
    with pytest.raises(duckdb.Error):
        load_all(con, bad, AS_OF)

    assert row_counts(con) == before
    segments = query_df(con, "SELECT segment FROM vic.dim_client ORDER BY client_id")
    assert segments["segment"].tolist() == ["cooperative_bank", "corporate"]


def test_missing_key_raises(con):
    data = small_data()
    del data["positions"]
    with pytest.raises(ValueError, match="positions"):
        load_all(con, data, AS_OF)


def test_build_dim_date_flags_by_hand():
    dim_date = build_dim_date(date(2026, 2, 27), date(2026, 3, 1))

    assert dim_date["date"].tolist() == [
        date(2026, 2, 27),
        date(2026, 2, 28),
        date(2026, 3, 1),
    ]
    assert dim_date["is_month_end"].tolist() == [False, True, False]
    # Fri, Sat, Sun
    assert dim_date["is_weekend"].tolist() == [False, True, True]
    assert dim_date["quarter"].tolist() == [1, 1, 1]


def test_build_dim_date_rejects_reversed_range():
    with pytest.raises(ValueError):
        build_dim_date(date(2026, 3, 2), date(2026, 3, 1))


def test_query_df_with_params(con):
    load_all(con, small_data(), AS_OF)
    df = query_df(
        con,
        "SELECT client_id FROM vic.fact_trades WHERE product_id = ? ORDER BY 1",
        ["P015"],
    )
    assert df["client_id"].tolist() == ["C0001", "C0002"]


def test_generated_data_loads_completely(con):
    data = generate_all(seed=42, as_of=AS_OF_DEFAULT)
    load_all(con, data, AS_OF_DEFAULT)

    counts = row_counts(con)
    for table, key in TABLE_TO_DATA_KEY.items():
        assert counts[table] == len(data[key])


def test_build_database_writes_file_named_like_schema(tmp_path, monkeypatch):
    """A file called vic.duckdb must not clash with the schema vic, also on rebuild."""
    rates = pd.DataFrame(
        {
            "rate_date": [AS_OF_DEFAULT, AS_OF_DEFAULT],
            "tenor_years": [2, 10],
            "rate_pct": [2.1, 2.7],
        }
    )
    monkeypatch.setattr("vic.db.fetch_rates", lambda as_of: rates)
    path = tmp_path / "sub" / "vic.duckdb"
    build_database(path, seed=1)
    build_database(path, seed=1)

    with get_connection(path) as file_con:
        counts = row_counts(file_con)
        rate_count = file_con.execute("SELECT count(*) FROM vic.market_rates")
        assert rate_count.fetchone() == (2,)
    assert counts["dim_product"] == 16
    assert counts["fact_trades"] > 0


INSERT_EVAL_RUN = """
INSERT INTO vic.reco_eval_runs VALUES
    (TIMESTAMP '2026-11-20 06:00:00', DATE '2026-11-20', 'baseline', 'new', 3, 72,
     0.25, 0.64, 0.51, 1.0)
"""


def eval_runs(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute("SELECT * FROM vic.reco_eval_runs ORDER BY ALL").fetchall()


def test_reload_keeps_eval_run_history(con):
    load_all(con, small_data(), AS_OF)
    con.execute(INSERT_EVAL_RUN)
    before = eval_runs(con)

    load_all(con, small_data(), AS_OF)
    load_all(con, small_data(), AS_OF)

    assert eval_runs(con) == before
    assert len(before) == 1


def test_failed_reload_keeps_eval_run_history(con):
    load_all(con, small_data(), AS_OF)
    con.execute(INSERT_EVAL_RUN)
    before = eval_runs(con)

    bad = small_data()
    bad["clients"].loc[0, "segment"] = "retail"
    with pytest.raises(duckdb.Error):
        load_all(con, bad, AS_OF)

    assert eval_runs(con) == before


def test_file_rebuild_keeps_eval_run_history(tmp_path, monkeypatch):
    """The file database is attached under its own catalog name; history must survive."""
    monkeypatch.setattr("vic.db.fetch_rates", lambda as_of: None)
    path = tmp_path / "vic.duckdb"
    build_database(path, seed=1)
    with get_connection(path) as file_con:
        file_con.execute(INSERT_EVAL_RUN)

    build_database(path, seed=1)

    with get_connection(path) as file_con:
        assert len(eval_runs(file_con)) == 1


# A second process holds the file like the app or the pipeline container would.
HOLD_FILE_SCRIPT = """
import sys, time, duckdb
con = duckdb.connect()
con.execute("ATTACH '" + sys.argv[1] + "' AS held")
print("locked", flush=True)
time.sleep(float(sys.argv[2]))
"""


def hold_file(path, seconds: float) -> subprocess.Popen:
    """Start a process that locks path for the given time; return once locked."""
    process = subprocess.Popen(
        [sys.executable, "-c", HOLD_FILE_SCRIPT, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "locked"
    return process


def test_locked_file_fails_without_timeout(tmp_path):
    path = tmp_path / "vic.duckdb"
    holder = hold_file(path, 30)
    try:
        with pytest.raises(duckdb.IOException, match="Could not set lock"):
            get_connection(path)
    finally:
        holder.kill()
        holder.wait()


def test_connection_waits_until_the_other_process_releases(tmp_path):
    path = tmp_path / "vic.duckdb"
    holder = hold_file(path, 1)
    start = time.monotonic()
    con = get_connection(path, lock_timeout_s=20)
    con.close()
    holder.wait()
    assert time.monotonic() - start > 0.5


def test_lock_wait_gives_up_after_the_timeout(tmp_path):
    path = tmp_path / "vic.duckdb"
    holder = hold_file(path, 30)
    try:
        with pytest.raises(duckdb.IOException, match="Could not set lock"):
            get_connection(path, lock_timeout_s=0.5)
    finally:
        holder.kill()
        holder.wait()
