import logging
from datetime import datetime

import pandas as pd
import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import get_connection, load_all, record_feedback
from vic.market.fetch_rates import load_fallback
from vic.pipeline import daily_run
from vic.quality.checks import QualityCheckError
from vic.ranking.score import TOP_N
from vic.signals.rules import MAX_RISK_LEVEL

RUN_TS = datetime(2026, 11, 20, 6, 0)


@pytest.fixture
def con():
    connection = get_connection(":memory:")
    yield connection
    connection.close()


def run(con):
    return daily_run.run_daily(con, AS_OF_DEFAULT, RUN_TS, market_rates=load_fallback())


def test_pipeline_produces_impulses_for_every_sales_rep(con, caplog):
    with caplog.at_level(logging.INFO, logger="vic.pipeline.daily_run"):
        impulses = run(con)

    reps = {
        r
        for (r,) in con.execute("SELECT sales_rep_id FROM vic.dim_sales_rep").fetchall()
    }
    stored = con.execute(
        "SELECT sales_rep_id, count(*) FROM vic_app.daily_impulses GROUP BY 1"
    ).fetchall()
    assert {rep for rep, _ in stored} == reps
    assert all(n == TOP_N for _, n in stored)
    assert len(impulses) == len(reps) * TOP_N
    assert "done: write impulses" in caplog.text

    checks = con.execute(
        "SELECT count(*), bool_and(passed) FROM vic_app.quality_results"
    ).fetchone()
    assert checks[0] > 0 and checks[1]


def test_rerun_same_day_replaces_impulses(con):
    first = run(con)
    second = run(con)

    count = con.execute("SELECT count(*) FROM vic_app.daily_impulses").fetchone()[0]
    assert count == len(second)
    assert first["impulse_id"].tolist() == second["impulse_id"].tolist()


def test_feedback_survives_reload_and_rerun(con):
    impulses = run(con)
    impulse_id = impulses.loc[0, "impulse_id"]
    record_feedback(con, impulse_id, AS_OF_DEFAULT, "angenommen", RUN_TS)

    load_all(con, generate_all(seed=42, as_of=AS_OF_DEFAULT), AS_OF_DEFAULT)
    run(con)

    feedback = con.execute(
        "SELECT impulse_id, status FROM vic_app.impulse_feedback"
    ).fetchall()
    assert feedback == [(impulse_id, "angenommen")]
    # The rerun recreates the same impulse id, so the feedback is still linked.
    linked = con.execute(
        """
        SELECT count(*) FROM vic_app.impulse_feedback f
        JOIN vic_app.daily_impulses i USING (impulse_id)
        """
    ).fetchone()
    assert linked == (1,)


def test_invalid_feedback_status_is_rejected(con):
    run(con)
    with pytest.raises(ValueError, match="status"):
        record_feedback(con, "x", AS_OF_DEFAULT, "vielleicht", RUN_TS)


def test_failed_quality_check_stops_before_load(con, monkeypatch):
    run(con)
    before = con.execute("SELECT count(*) FROM vic.fact_trades").fetchone()

    real_generate = daily_run.generate_all

    def broken_generate(seed, as_of):
        data = real_generate(seed=seed, as_of=as_of)
        data["clients"].loc[0, "segment"] = "retail"
        return data

    monkeypatch.setattr(daily_run, "generate_all", broken_generate)
    with pytest.raises(QualityCheckError, match="segment"):
        run(con)

    assert con.execute("SELECT count(*) FROM vic.fact_trades").fetchone() == before
    failed = con.execute(
        "SELECT dataset, check_name FROM vic_app.quality_results WHERE NOT passed"
    ).fetchall()
    assert failed == [("clients", "segment.allowed_values")]


def test_cli_parses_as_of(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        daily_run, "run_daily", lambda con, as_of, run_ts, seed: calls.append(as_of)
    )
    daily_run.main(["--as-of", "2026-11-19", "--db", str(tmp_path / "vic.duckdb")])
    assert [d.isoformat() for d in calls] == ["2026-11-19"]


def test_keep_suitable_reranks_after_filtering():
    recs = pd.DataFrame(
        [("C1", "P8", 0.9, 1), ("C1", "P1", 0.5, 2), ("C1", "P2", 0.4, 3)],
        columns=["client_id", "product_id", "score", "rank"],
    )
    suitable = pd.DataFrame({"client_id": ["C1", "C1"], "product_id": ["P1", "P2"]})
    kept = daily_run.keep_suitable(recs, suitable, k=1)

    # P8 is not suitable; P1 moves up to rank 1 (used for reactivation).
    assert kept[["product_id", "rank"]].values.tolist() == [["P1", 1]]


def test_no_impulse_is_unsuitable(con):
    run(con)
    impulses = con.execute(
        """
        SELECT CAST(c.risk_profile AS VARCHAR), c.has_floating_rate_debt,
               p.risk_level, p.is_hedging_product, CAST(p.asset_class AS VARCHAR)
        FROM vic_app.daily_impulses AS i
        JOIN vic.dim_client AS c USING (client_id)
        JOIN vic.dim_product AS p USING (product_id)
        """
    ).fetchall()
    assert impulses
    for profile, floating, risk, hedging, asset_class in impulses:
        if hedging:
            assert floating or asset_class != "rates_derivative"
        else:
            assert risk <= MAX_RISK_LEVEL[profile]
