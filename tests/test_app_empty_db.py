"""Every page must render (with a hint) before the daily run built the database."""

import json
from datetime import datetime

import pandas as pd
from plotly.utils import PlotlyJSONEncoder

from app import shell as app_shell
from app.components import NO_DATA_TEXT
from app.pages import kunde, markt, monitoring, tagesimpulse
from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import ensure_app_schema, get_connection, load_all
from vic.quality.checks import store_quality_results

RUN_TS = datetime(2026, 11, 20, 6, 0)


def render(component) -> str:
    return json.dumps(
        component.to_plotly_json(), cls=PlotlyJSONEncoder, ensure_ascii=False
    )


def all_layouts() -> dict[str, str]:
    return {
        "tagesimpulse": render(tagesimpulse.layout()),
        "kunde_suche": render(kunde.layout(client_id=kunde.SEARCH_SLUG)),
        "kunde": render(kunde.layout(client_id="C0001")),
        "markt": render(markt.layout()),
        "monitoring": render(monitoring.layout()),
    }


def test_fresh_database_every_page_shows_a_hint(app_connection):
    app_connection(get_connection(":memory:"))

    pages = all_layouts()

    for name in ("tagesimpulse", "kunde_suche", "kunde", "markt"):
        assert NO_DATA_TEXT in pages[name], name
    assert "Noch kein Feedback erfasst." in pages["monitoring"]
    assert "Noch kein Evaluationslauf" in pages["monitoring"]
    assert "Noch keine Qualitätsprüfung gelaufen." in pages["monitoring"]
    title, rep, stand, *_ = app_shell.update_shell("/markt", None)
    assert (title, rep, stand) == (
        "Markt-Monitor",
        app_shell.ALL_REPS_LABEL,
        "Stand: –",
    )


def test_failed_first_run_still_shows_quality_results(app_connection):
    """Quality failed before loading: vic_app exists, schema vic does not."""
    con = app_connection(get_connection(":memory:"))
    ensure_app_schema(con)
    summary = pd.DataFrame(
        [
            {
                "dataset": "clients",
                "check": "segment.allowed_values",
                "passed": False,
                "failures": 3,
            },
            {
                "dataset": "clients",
                "check": "client_id.unique",
                "passed": True,
                "failures": 0,
            },
        ]
    )
    store_quality_results(con, summary, RUN_TS, AS_OF_DEFAULT)

    pages = all_layouts()

    assert "segment.allowed_values" in pages["monitoring"]
    assert "1 von 2 Prüfungen fehlgeschlagen" in pages["monitoring"]
    assert NO_DATA_TEXT in pages["markt"]
    assert NO_DATA_TEXT in pages["kunde"]


def test_star_schema_without_daily_run(app_connection):
    """Built with python -m vic.db: data exists, impulses and rates do not."""
    con = app_connection(get_connection(":memory:"))
    load_all(con, generate_all(seed=42, as_of=AS_OF_DEFAULT), AS_OF_DEFAULT)

    pages = all_layouts()

    assert NO_DATA_TEXT in pages["tagesimpulse"]
    assert "client-picker" in pages["kunde_suche"]
    assert NO_DATA_TEXT in pages["kunde_suche"]  # no impulses for the quick links
    assert "Stammdaten" in pages["kunde"]
    assert "Keine Marktdaten geladen." in pages["markt"]
