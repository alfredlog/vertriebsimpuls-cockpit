"""Redesign checks: every page renders, shared template, formats, WCAG contrast."""

import json
from datetime import date

import pandas as pd
import plotly.io as pio
import pytest
from dash import dcc
from plotly.utils import PlotlyJSONEncoder

from app import data
from app import shell as app_shell
from app.charts import ACCENT, INK_MUTED, SIGNAL_COLORS, TEMPLATE_NAME
from app.format import SIGNAL_LABELS, fmt_bp, fmt_date, fmt_eur, fmt_pct, fmt_rate
from app.pages import kunde, markt, monitoring, tagesimpulse
from vic.data.generate import AS_OF_DEFAULT

WCAG_AA_TEXT = 4.5


@pytest.fixture
def con(app_db):
    """The app's own connection to the pipeline-built test database."""
    return app_db


def render(component) -> str:
    return json.dumps(
        component.to_plotly_json(), cls=PlotlyJSONEncoder, ensure_ascii=False
    )


def graphs(component) -> list[dcc.Graph]:
    """All dcc.Graph components inside a layout tree."""
    found = []
    stack = [component]
    while stack:
        node = stack.pop()
        if isinstance(node, dcc.Graph):
            found.append(node)
        children = getattr(node, "children", None)
        if isinstance(children, list | tuple):
            stack.extend(children)
        elif children is not None and hasattr(children, "to_plotly_json"):
            stack.append(children)
    return found


def all_pages(con) -> dict:
    client_id = con.execute(
        "SELECT client_id FROM vic_app.daily_impulses ORDER BY score DESC LIMIT 1"
    ).fetchone()[0]
    return {
        "tagesimpulse": tagesimpulse.layout(),
        "kunde": kunde.layout(client_id=client_id),
        "kunde_suche": kunde.layout(client_id=kunde.SEARCH_SLUG),
        "kunde_unbekannt": kunde.layout(client_id="C9999"),
        "markt": markt.layout(),
        "monitoring": monitoring.layout(),
    }


def test_every_page_renders(con):
    rendered = {name: render(page) for name, page in all_pages(con).items()}

    assert "Impulse heute" in rendered["tagesimpulse"]
    assert "Annahmequote 30 Tage" in rendered["tagesimpulse"]
    assert "Produktmix" in rendered["kunde"]
    assert "client-picker" in rendered["kunde_suche"]
    assert "Kunde nicht gefunden" in rendered["kunde_unbekannt"]
    assert "10 Jahre" in rendered["markt"]
    assert "Datenqualitätsprüfungen" in rendered["monitoring"]


def test_every_chart_uses_the_shared_template(con):
    figures = [g.figure for page in all_pages(con).values() for g in graphs(page)]

    assert len(figures) >= 4  # history, mix, curve, reco (+ acceptance with feedback)
    shared = pio.templates[TEMPLATE_NAME]
    for figure in figures:
        assert figure.layout.template == shared


@pytest.mark.parametrize(
    ("path", "title", "active"),
    [
        ("/", "Tagesimpulse", "nav-tagesimpulse"),
        ("/kunde/suche", "Kunde 360°", "nav-kunde"),
        ("/kunde/C0001", "Kunde 360°", "nav-kunde"),
        ("/markt", "Markt-Monitor", "nav-markt"),
        ("/monitoring", "Monitoring", "nav-monitoring"),
    ],
)
def test_shell_shows_title_active_item_and_stand(con, path, title, active):
    rep_id = con.execute("SELECT min(sales_rep_id) FROM vic.dim_sales_rep").fetchone()[
        0
    ]
    rep_name = con.execute(
        "SELECT name FROM vic.dim_sales_rep WHERE sales_rep_id = ?", [rep_id]
    ).fetchone()[0]

    shown_title, shown_rep, stand, *classes = app_shell.update_shell(path, rep_id)

    assert shown_title == title
    assert shown_rep == rep_name
    assert stand == "Stand: 20.11.2026"
    ids = [f"nav-{key}" for key, *_ in app_shell.NAV_ITEMS]
    assert [c == app_shell.NAV_ACTIVE for c in classes] == [i == active for i in ids]


def test_shell_without_rep_shows_all(con):
    assert app_shell.update_shell("/", None)[1] == app_shell.ALL_REPS_LABEL


def test_client_picker_navigates():
    assert app_shell.open_client("C0042") == "/kunde/C0042"


def test_empty_state_toggles_with_table_data():
    assert tagesimpulse.toggle_empty_state([]) == {"display": "block"}
    assert tagesimpulse.toggle_empty_state([{"id": "x"}]) == {"display": "none"}


def test_signal_rows_keep_text_and_chip(con):
    rows = tagesimpulse.table_rows(AS_OF_DEFAULT, None, None, None)
    assert {"signal", "signal_chip", "score_value"} <= set(rows[0])
    assert rows[0]["signal"] in rows[0]["signal_chip"]


def test_german_formats():
    assert fmt_eur(1_234_567) == "1.234.567 €"
    assert fmt_rate(3.25) == "3,25 %"
    assert fmt_pct(0.0325, 2) == "3,25 %"
    assert fmt_bp(9.66) == "+9,7 bp"
    assert fmt_date(date(2026, 11, 20)) == "20.11.2026"


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio."""
    lighter, darker = sorted(
        [_luminance(foreground), _luminance(background)], reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_text_colors_meet_wcag_aa():
    assert set(SIGNAL_COLORS) == set(SIGNAL_LABELS)
    for signal_type, (_dot, text, background) in SIGNAL_COLORS.items():
        assert contrast(text, background) >= WCAG_AA_TEXT, signal_type
    assert contrast(INK_MUTED, "#ffffff") >= WCAG_AA_TEXT
    assert contrast(ACCENT, "#ffffff") >= WCAG_AA_TEXT


def test_table_rows_escape_html_from_the_database(monkeypatch):
    """The table renders markdown with html=True; DB values must not inject HTML."""
    malicious = pd.DataFrame(
        [
            {
                "impulse_id": "2026-11-20|C1|P1",
                "rank": 1,
                "rep_name": "<script>alert(1)</script>",
                "client_id": "C1",
                "client_name": "Evil <img src=x onerror=alert(1)> [link](javascript:x)",
                "product_name": "<b>P</b>",
                "signal_type": "maturity",
                "score": 0.9,
                "reason_de": "<i>Grund</i>",
                "status": None,
            }
        ]
    )
    monkeypatch.setattr(data, "impulses", lambda *args: malicious)

    row = tagesimpulse.table_rows(AS_OF_DEFAULT, None, None, None)[0]
    rendered = row["client"] + row["impulse"] + row["signal_chip"]

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "<i>" not in rendered
    assert "](javascript:" not in rendered
    assert "&lt;script&gt;" in row["client"]
    assert row["client"].count("](/kunde/") == 1
