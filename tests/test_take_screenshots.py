"""The screenshot script without a browser: demo data, startup checks, page list."""

import importlib.util
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from vic.data.generate import AS_OF_DEFAULT
from vic.db import get_connection

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "take_screenshots.py"
_spec = importlib.util.spec_from_file_location("take_screenshots", SCRIPT)
shots_script = importlib.util.module_from_spec(_spec)
# Registered so the conftest guard also wraps this module's get_connection.
sys.modules["take_screenshots"] = shots_script
_spec.loader.exec_module(shots_script)


def test_demo_run_dates_are_weekly_and_end_at_as_of():
    assert shots_script.demo_run_dates(date(2026, 11, 20), 3) == [
        date(2026, 11, 6),
        date(2026, 11, 13),
        date(2026, 11, 20),
    ]


def test_demo_database_has_feedback_and_a_maturity_client(tmp_path):
    db_path = tmp_path / "vic.duckdb"
    client_id = shots_script.build_demo_database(db_path, AS_OF_DEFAULT, weeks=2)

    con = get_connection(db_path)
    try:
        days = con.execute(
            "SELECT count(DISTINCT run_date) FROM vic_app.daily_impulses"
        ).fetchone()[0]
        feedback = con.execute(
            "SELECT count(*), count(DISTINCT status) FROM vic_app.impulse_feedback"
        ).fetchone()
        runs = con.execute(
            "SELECT count(DISTINCT run_ts) FROM vic.reco_eval_runs"
        ).fetchone()[0]
        soon = con.execute(
            """
            SELECT count(*) FROM vic.fact_positions AS p, vic.run_context AS rc
            WHERE p.client_id = ? AND p.maturity_date > rc.as_of
                AND p.maturity_date <= rc.as_of + 30
            """,
            [client_id],
        ).fetchone()[0]
    finally:
        con.close()

    assert days == 2
    assert feedback[0] > 0 and feedback[1] == 2  # both statuses present
    assert runs == 2
    assert soon >= 1  # Kunde 360° shows a highlighted maturity


def test_free_port_is_bindable():
    port = shots_script.free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_wait_for_http_reports_a_crashed_app(tmp_path):
    log_path = tmp_path / "app.log"
    log_path.write_text("Traceback: boom\n")
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    process.wait(timeout=10)

    with pytest.raises(RuntimeError, match="exited with code 3") as error:
        shots_script.wait_for_http("http://127.0.0.1:9/", process, log_path, 5)
    assert "boom" in str(error.value)


def test_every_page_waits_for_its_charts_and_table():
    shots = {shot.file_name: shot for shot in shots_script.shots("C0001")}

    assert set(shots) == {
        "tagesimpulse.png",
        "kunde360.png",
        "markt.png",
        "monitoring.png",
    }
    assert shots["kunde360.png"].path == "/kunde/C0001"
    assert shots["kunde360.png"].plots == 2
    assert shots["markt.png"].plots == 1
    assert shots["monitoring.png"].plots == 2
    assert shots["tagesimpulse.png"].table_selector == "#impulse-table td"
