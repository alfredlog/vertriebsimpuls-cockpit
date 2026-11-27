import json
from datetime import datetime

import dash
import pytest
from plotly.utils import PlotlyJSONEncoder

from app import app as dash_app
from app.components import FOOTER_TEXT, NO_DATA_TEXT
from app.pages import kunde, markt, monitoring, tagesimpulse
from vic.data.generate import AS_OF_DEFAULT
from vic.db import get_connection, record_feedback

RUN_TS = datetime(2026, 11, 20, 6, 0)


def render(component) -> str:
    """Serialize like Dash does; fails on anything the browser could not receive."""
    return json.dumps(
        component.to_plotly_json(), cls=PlotlyJSONEncoder, ensure_ascii=False
    )


@pytest.fixture
def con(app_db):
    """The app's own connection to the pipeline-built test database."""
    return app_db


def test_app_registers_all_pages():
    paths = {
        page["path_template"] or page["path"] for page in dash.page_registry.values()
    }
    assert paths == {"/", "/kunde/<client_id>", "/markt", "/monitoring"}
    assert dash_app.title == "Vertriebsimpuls-Cockpit"


def test_health_endpoint_answers_without_database():
    response = dash_app.server.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_shell_has_footer_and_navigation():
    shell = render(dash_app.layout)
    assert FOOTER_TEXT in shell
    for label in ("Tagesimpulse", "Markt-Monitor", "Monitoring"):
        assert label in shell


def test_every_page_layout_renders(con):
    client_id = con.execute(
        "SELECT client_id FROM vic_app.daily_impulses ORDER BY score DESC LIMIT 1"
    ).fetchone()[0]
    pages = {
        "tagesimpulse": tagesimpulse.layout(),
        "kunde": kunde.layout(client_id=client_id),
        "markt": markt.layout(),
        "monitoring": monitoring.layout(),
    }
    rendered = {name: render(page) for name, page in pages.items()}

    assert "impulse-table" in rendered["tagesimpulse"]
    assert "Stammdaten" in rendered["kunde"]
    assert "Zinsstrukturkurve" in rendered["markt"]
    assert "Empfehlungsmodelle" in rendered["monitoring"]


def test_unknown_client_shows_message(con):
    assert "Kunde nicht gefunden" in render(kunde.layout(client_id="C9999"))


def test_monitoring_with_feedback_renders(con):
    impulse_id = con.execute(
        "SELECT min(impulse_id) FROM vic_app.daily_impulses"
    ).fetchone()[0]
    record_feedback(con, impulse_id, AS_OF_DEFAULT, "abgelehnt", RUN_TS)
    try:
        assert "acceptance-table" in render(monitoring.layout())
    finally:
        con.execute("DELETE FROM vic_app.impulse_feedback")


def test_empty_database_shows_hint(app_connection):
    app_connection(get_connection(":memory:"))
    assert NO_DATA_TEXT in render(tagesimpulse.layout())


def test_filter_callback_returns_only_matching_rows(con):
    rows = tagesimpulse.filter_impulses(
        None, None, "maturity", AS_OF_DEFAULT.isoformat()
    )
    assert rows
    assert {row["signal"] for row in rows} == {"Fälligkeit"}


def test_feedback_callback_writes_one_row(con):
    rows = tagesimpulse.table_rows(AS_OF_DEFAULT, None, None, None)
    target = rows[3]
    active_cell = {
        "row": 3,
        "column": 8,
        "column_id": tagesimpulse.ACCEPT_COLUMN,
        "row_id": target["id"],
    }
    try:
        updated, cell, message = tagesimpulse.record_feedback_click(
            active_cell, rows, AS_OF_DEFAULT.isoformat()
        )

        stored = con.execute(
            "SELECT impulse_id, run_date, status FROM vic_app.impulse_feedback"
        ).fetchall()
        assert stored == [(target["id"], AS_OF_DEFAULT, "angenommen")]
        assert updated[3]["status"] == "angenommen"
        assert [r["status"] for i, r in enumerate(updated) if i != 3] == [
            r["status"] for i, r in enumerate(rows) if i != 3
        ]
        assert cell is None
        assert target["client_name"] in message
    finally:
        con.execute("DELETE FROM vic_app.impulse_feedback")


def test_click_on_other_cell_writes_nothing(con):
    rows = tagesimpulse.table_rows(AS_OF_DEFAULT, None, None, None)
    cell = {"row": 0, "column": 2, "column_id": "client", "row_id": rows[0]["id"]}

    result = tagesimpulse.record_feedback_click(cell, rows, AS_OF_DEFAULT.isoformat())

    assert result == (dash.no_update, dash.no_update, dash.no_update)
    assert con.execute("SELECT count(*) FROM vic_app.impulse_feedback").fetchone() == (
        0,
    )
