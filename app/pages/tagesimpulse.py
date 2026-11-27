"""Tagesimpulse: KPIs, filters and today's ranked impulses with feedback buttons."""

from datetime import UTC, date, datetime
from html import escape

import dash
import pandas as pd
from dash import Input, Output, State, callback, dcc, html

from app import data
from app.components import (
    NO_DATA_TEXT,
    STATUS_STYLES,
    card,
    empty_state,
    kpi_card,
    score_bar_styles,
    signal_chip_markdown,
    table,
)
from app.format import (
    NO_VALUE,
    SEGMENT_LABELS,
    SIGNAL_LABELS,
    STATUS_OPEN,
    fmt_bp,
    fmt_number,
    fmt_pct,
    fmt_rate,
)
from vic.ranking.score import AGREEMENT_BONUS, W_RECO, W_RULE

dash.register_page(__name__, path="/", name="Tagesimpulse", title="Tagesimpulse")

ACCEPT_COLUMN = "accept"
REJECT_COLUMN = "reject"
FEEDBACK_BY_COLUMN = {ACCEPT_COLUMN: "angenommen", REJECT_COLUMN: "abgelehnt"}
# Icon "buttons" inside the table; the column header names the action.
ACTION_ICONS = {ACCEPT_COLUMN: "✓", REJECT_COLUMN: "✕"}
MAX_SCORE = W_RULE + W_RECO + AGREEMENT_BONUS
ACCEPTANCE_WINDOW_DAYS = 30
KPI_TENOR = 10

COLUMNS = [
    {"id": "rank", "name": "#"},
    {"id": "client", "name": "Kunde", "presentation": "markdown"},
    {"id": "impulse", "name": "Produkt und Begründung", "presentation": "markdown"},
    {"id": "signal_chip", "name": "Signal", "presentation": "markdown"},
    {"id": "score_text", "name": "Score"},
    {"id": "status", "name": "Status"},
    {"id": ACCEPT_COLUMN, "name": "Annehmen"},
    {"id": REJECT_COLUMN, "name": "Ablehnen"},
]

STYLE_DATA_CONDITIONAL = [
    {"if": {"state": "active"}, "backgroundColor": "white", "border": "none"},
    {"if": {"state": "selected"}, "backgroundColor": "white"},
    {"if": {"column_id": "rank"}, "color": "#64748b", "textAlign": "right"},
    {"if": {"column_id": "score_text"}, "fontWeight": "600", "textAlign": "right"},
    *STATUS_STYLES,
    *score_bar_styles("score_text", "score_value", MAX_SCORE),
]


def _fixed(column_id: str, width: int, **style) -> dict:
    """Pin a column width; DataTable only honours width with min/max set."""
    px = f"{width}px"
    return {
        "if": {"column_id": column_id},
        "width": px,
        "minWidth": px,
        "maxWidth": px,
        **style,
    }


# Fixed narrow columns; "impulse" takes the rest, so the table fits 1280 px.
STYLE_CELL_CONDITIONAL = [
    _fixed("rank", 32),
    _fixed("client", 124),
    _fixed("signal_chip", 124),
    _fixed("score_text", 56),
    _fixed("status", 104),
    _fixed(ACCEPT_COLUMN, 72, textAlign="center"),
    _fixed(REJECT_COLUMN, 72, textAlign="center"),
    {"if": {"column_id": "impulse"}, "minWidth": "260px"},
]


def _action_css(column: str, color: str, hover_bg: str, active_bg: str) -> list[dict]:
    cell = f'td[data-dash-column="{column}"]'
    return [
        {
            "selector": cell,
            "rule": "cursor: pointer; vertical-align: middle !important;",
        },
        {
            "selector": f"{cell} .dash-cell-value",
            "rule": (
                "display: inline-flex; align-items: center; justify-content: center;"
                f"width: 28px; height: 28px; border-radius: 6px; color: {color};"
                "border: 1px solid #cbd5e1; font-weight: 700; background: white;"
                "transition: background-color .12s, border-color .12s;"
            ),
        },
        {
            "selector": f"{cell}:hover .dash-cell-value",
            "rule": f"background: {hover_bg}; border-color: {color};",
        },
        {
            "selector": f"{cell}:active .dash-cell-value",
            "rule": f"background: {active_bg}; transform: translateY(1px);",
        },
    ]


TABLE_EXTRA_CSS = [
    *_action_css(ACCEPT_COLUMN, "#15803d", "#dcfce7", "#bbf7d0"),
    *_action_css(REJECT_COLUMN, "#b91c1c", "#fee2e2", "#fecaca"),
    {
        "selector": ".dash-cell-value a",
        "rule": "color: #0f766e; font-weight: 600; text-decoration: none;",
    },
    {"selector": ".dash-cell-value a:hover", "rule": "text-decoration: underline;"},
    {"selector": "tr:hover td", "rule": "background-color: #f8fafc !important;"},
]


def table_rows(
    run_date: date, rep: str | None, segment: str | None, signal: str | None
):
    """Return the impulses as DataTable rows; 'id' is the impulse_id (row_id)."""
    impulses = data.impulses(run_date, rep, segment, signal)
    return [
        {
            "id": row.impulse_id,
            "rank": row.rank,
            "rep_name": row.rep_name,
            # Betreuer as a muted second line saves a column at 1280 px.
            "client": (
                f"[{row.client_name}](/kunde/{row.client_id})<br>"
                f'<span style="color:#64748b;font-size:12px">{row.rep_name}</span>'
            ),
            "client_name": row.client_name,
            "product_name": row.product_name or NO_VALUE,
            # Product and reason share one cell: CRM-style two-line row.
            "impulse": (
                f"<b>{escape(row.product_name or NO_VALUE)}</b><br>"
                f'<span style="color:#475569">{escape(row.reason_de)}</span>'
            ),
            "signal": SIGNAL_LABELS.get(row.signal_type, row.signal_type),
            "signal_chip": signal_chip_markdown(row.signal_type),
            "score_text": fmt_number(row.score, 2),
            "score_value": float(row.score),
            "reason_de": row.reason_de,
            "status": row.status if isinstance(row.status, str) else STATUS_OPEN,
            ACCEPT_COLUMN: ACTION_ICONS[ACCEPT_COLUMN],
            REJECT_COLUMN: ACTION_ICONS[REJECT_COLUMN],
        }
        for row in impulses.itertuples()
    ]


def kpi_row(run_date: date) -> html.Div:
    """Four KPI tiles: impulses, maturities, acceptance rate, 10Y rate move."""
    impulses = data.impulses(run_date)
    n_reps = impulses["sales_rep_id"].nunique()
    n_maturities = int((impulses["signal_type"] == "maturity").sum())

    rates = data.acceptance_by_week()
    window_start = pd.Timestamp(run_date) - pd.Timedelta(days=ACCEPTANCE_WINDOW_DAYS)
    recent = rates[pd.to_datetime(rates["week"]) >= window_start]
    decided = int(recent["decided"].sum())
    if decided:
        acceptance = fmt_pct(recent["accepted"].sum() / decided, 0)
        acceptance_sub = f"{int(recent['accepted'].sum())} von {decided} bewertet"
    else:
        acceptance, acceptance_sub = NO_VALUE, "noch kein Feedback"

    changes = data.rate_changes()
    tenor = changes[changes["tenor_years"] == KPI_TENOR]
    if tenor.empty or tenor["change_14d_bp"].isna().all():
        rate_value, rate_sub, trend = NO_VALUE, "keine Marktdaten", None
    else:
        change = float(tenor["change_14d_bp"].iloc[0])
        rate_value = fmt_bp(change)
        rate_sub = f"{KPI_TENOR}J aktuell {fmt_rate(float(tenor['rate_pct'].iloc[0]))}"
        trend = "up" if change > 0 else "down" if change < 0 else None

    return html.Div(
        className="grid grid-cols-4 gap-4 mb-4",
        children=[
            kpi_card(
                "Impulse heute", fmt_number(len(impulses), 0), f"für {n_reps} Betreuer"
            ),
            kpi_card(
                "Fälligkeiten ≤ 30 Tage",
                fmt_number(n_maturities, 0),
                "Wiederanlage-Impulse in der Liste",
            ),
            kpi_card("Annahmequote 30 Tage", acceptance, acceptance_sub),
            kpi_card(f"Δ {KPI_TENOR}J-Zins 14 Tage", rate_value, rate_sub, trend),
        ],
    )


def _options(labels: dict[str, str]) -> list[dict]:
    return [{"label": label, "value": key} for key, label in labels.items()]


def layout(**_kwargs) -> html.Div:
    """Page layout; data for the table is filled by the filter callback."""
    if not data.has_impulses():
        return html.Div(empty_state(NO_DATA_TEXT))

    run_date = data.latest_run_date()
    reps = data.sales_reps()
    rep_options = [
        {"label": rep.name, "value": rep.sales_rep_id} for rep in reps.itertuples()
    ]
    dropdown_class = "w-56 text-sm"
    filters = html.Div(
        className="flex items-center gap-2",
        children=[
            dcc.Dropdown(
                id="filter-rep",
                options=rep_options,
                placeholder="Alle Betreuer",
                className=dropdown_class,
            ),
            dcc.Dropdown(
                id="filter-segment",
                options=_options(SEGMENT_LABELS),
                placeholder="Alle Segmente",
                className=dropdown_class,
            ),
            dcc.Dropdown(
                id="filter-signal",
                options=_options(SIGNAL_LABELS),
                placeholder="Alle Signaltypen",
                className=dropdown_class,
            ),
        ],
    )
    return html.Div(
        [
            dcc.Store(id="run-date", data=run_date.isoformat()),
            kpi_row(run_date),
            card(
                "Impulse (Top 10 je Betreuer)",
                table(
                    "impulse-table",
                    COLUMNS,
                    table_rows(run_date, None, None, None),
                    markdown_options={"link_target": "_self", "html": True},
                    sort_action="native",
                    page_size=20,
                    style_data_conditional=STYLE_DATA_CONDITIONAL,
                    style_cell_conditional=STYLE_CELL_CONDITIONAL,
                    css=TABLE_EXTRA_CSS,
                    # Action headers in normal case so they fit 72 px.
                    style_header_conditional=[
                        {
                            "if": {"column_id": [ACCEPT_COLUMN, REJECT_COLUMN]},
                            "textTransform": "none",
                            "letterSpacing": "0",
                            "textAlign": "center",
                        }
                    ],
                    tooltip_header={
                        ACCEPT_COLUMN: "Impuls als angenommen markieren",
                        REJECT_COLUMN: "Impuls als abgelehnt markieren",
                        "score_text": "Regel (70 %) + Empfehlung (30 %) + Bonus",
                    },
                ),
                html.Div(
                    id="impulse-empty",
                    style={"display": "none"},
                    children=empty_state("Keine Impulse für diese Filter."),
                    className="mt-3",
                ),
                html.Div(
                    id="feedback-message",
                    className="text-sm text-slate-600 mt-3 min-h-5",
                    role="status",
                ),
                right=filters,
            ),
        ]
    )


@callback(Output("impulse-empty", "style"), Input("impulse-table", "data"))
def toggle_empty_state(rows: list[dict] | None):
    """Show a message instead of an empty table body when filters match nothing."""
    return {"display": "none"} if rows else {"display": "block"}


@callback(
    Output("impulse-table", "data"),
    Input("filter-rep", "value"),
    Input("filter-segment", "value"),
    Input("filter-signal", "value"),
    State("run-date", "data"),
    prevent_initial_call=True,
)
def filter_impulses(
    rep: str | None, segment: str | None, signal: str | None, run_date: str
):
    """Reload the table when a filter changes."""
    return table_rows(date.fromisoformat(run_date), rep, segment, signal)


@callback(
    Output("impulse-table", "data", allow_duplicate=True),
    Output("impulse-table", "active_cell"),
    Output("feedback-message", "children"),
    Input("impulse-table", "active_cell"),
    State("impulse-table", "data"),
    State("run-date", "data"),
    prevent_initial_call=True,
)
def record_feedback_click(active_cell: dict | None, rows: list[dict], run_date: str):
    """Write feedback when an 'angenommen' / 'abgelehnt' cell is clicked.

    active_cell is reset so a second click on the same cell fires again.
    """
    if not active_cell or active_cell.get("column_id") not in FEEDBACK_BY_COLUMN:
        return dash.no_update, dash.no_update, dash.no_update

    status = FEEDBACK_BY_COLUMN[active_cell["column_id"]]
    impulse_id = active_cell["row_id"]
    data.save_feedback(
        impulse_id,
        date.fromisoformat(run_date),
        status,
        datetime.now(UTC).replace(tzinfo=None),
    )
    updated = [
        {**row, "status": status} if row["id"] == impulse_id else row for row in rows
    ]
    client_name = next(row["client_name"] for row in rows if row["id"] == impulse_id)
    return updated, None, f"Gespeichert: {client_name} – {status}"
