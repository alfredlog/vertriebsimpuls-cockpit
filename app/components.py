"""Layout building blocks: cards, KPI tiles, chips, dense tables.

Spacing scale: 4 px steps; cards p-4, gaps 4 (16 px). Text: slate-900 body,
slate-500 secondary (4.8:1 on white). One accent: teal-700.
"""

from dash import dash_table, html

from app.charts import INK_MUTED, SIGNAL_COLORS, STATUS_BAD, STATUS_GOOD
from app.format import SIGNAL_LABELS
from app.icons import icon

FOOTER_TEXT = "Unabhängiges Demo-Projekt – alle Daten fiktiv"
NO_DATA_TEXT = (
    "Noch keine Tagesimpulse vorhanden. Bitte zuerst den Tageslauf starten: "
    "uv run python -m vic.pipeline.daily_run"
)

TABLE_STYLE = {
    "style_table": {"overflowX": "auto", "minWidth": "100%"},
    "style_cell": {
        "fontFamily": "Inter, ui-sans-serif, system-ui, sans-serif",
        "fontSize": "13px",
        "fontVariantNumeric": "tabular-nums",
        "padding": "7px 8px",
        "textAlign": "left",
        "border": "none",
        "borderBottom": "1px solid #e2e8f0",
        "color": "#0f172a",
        "backgroundColor": "white",
        "whiteSpace": "normal",
        "height": "auto",
        "lineHeight": "1.45",
        "verticalAlign": "top",
    },
    "style_header": {
        "fontSize": "11px",
        "fontWeight": "600",
        "textTransform": "uppercase",
        "letterSpacing": "0.04em",
        "backgroundColor": "#f8fafc",
        "color": "#475569",
        "borderBottom": "1px solid #cbd5e1",
        "padding": "8px 8px",
    },
}

# Readable selection: no default pink highlight on the clicked cell.
TABLE_CSS = [
    {
        "selector": "td.cell--selected, td.focused",
        "rule": "background-color: inherit !important; border-color: #e2e8f0 !important;",
    },
    {
        "selector": ".dash-spreadsheet-container .dash-spreadsheet-inner td div p",
        "rule": "margin: 0;",
    },
    {"selector": ".dash-table-tooltip", "rule": "font-size: 12px;"},
]

STATUS_STYLES = [
    {
        "if": {"filter_query": '{status} = "angenommen"', "column_id": "status"},
        "color": STATUS_GOOD,
        "fontWeight": "600",
    },
    {
        "if": {"filter_query": '{status} = "abgelehnt"', "column_id": "status"},
        "color": STATUS_BAD,
        "fontWeight": "600",
    },
    {
        "if": {"filter_query": '{status} = "offen"', "column_id": "status"},
        "color": INK_MUTED,
    },
]


def card(
    title: str | None, *children: object, right: object = None, class_name: str = ""
) -> html.Section:
    """White panel with an optional header row (title left, controls right)."""
    header = []
    if title is not None:
        header = [
            html.Div(
                className="flex items-center justify-between gap-3 mb-3",
                children=[
                    html.H2(title, className="text-sm font-semibold text-slate-900"),
                    right,
                ],
            )
        ]
    return html.Section(
        className=f"bg-white rounded-lg border border-slate-200 shadow-sm p-4 {class_name}",
        children=[*header, *children],
    )


def kpi_card(
    label: str, value: str, sub: str = "", trend: str | None = None
) -> html.Div:
    """KPI tile; trend 'up'/'down' adds an arrow and color next to the value."""
    value_children: list = [html.Span(value)]
    value_class = "text-slate-900"
    if trend == "up":
        value_children.insert(0, icon("arrow_up", "w-5 h-5"))
        value_class = "text-green-700"
    elif trend == "down":
        value_children.insert(0, icon("arrow_down", "w-5 h-5"))
        value_class = "text-red-700"
    return html.Div(
        className="bg-white rounded-lg border border-slate-200 shadow-sm px-4 py-3",
        children=[
            html.Div(
                label,
                className="text-xs font-medium text-slate-500 uppercase tracking-wide",
            ),
            html.Div(
                value_children,
                className=f"mt-1 flex items-center gap-1 text-2xl font-semibold tabular-nums {value_class}",
            ),
            html.Div(sub, className="mt-0.5 text-xs text-slate-500 min-h-4"),
        ],
    )


def chip(
    text: str,
    text_color: str = "#334155",
    background: str = "#f1f5f9",
    dot: str | None = None,
) -> html.Span:
    """Small rounded label; dot shows the matching chart color."""
    children: list = [text]
    if dot:
        children.insert(
            0,
            html.Span(
                className="inline-block w-1.5 h-1.5 rounded-full",
                style={"backgroundColor": dot},
            ),
        )
    return html.Span(
        children,
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap",
        style={"color": text_color, "backgroundColor": background},
    )


def signal_chip(signal_type: str) -> html.Span:
    """Chip in the signal type's fixed color (same on every page)."""
    dot, text_color, background = SIGNAL_COLORS[signal_type]
    return chip(
        SIGNAL_LABELS.get(signal_type, signal_type), text_color, background, dot
    )


def signal_chip_markdown(signal_type: str) -> str:
    """The same chip as HTML for DataTable markdown cells."""
    dot, text_color, background = SIGNAL_COLORS[signal_type]
    label = SIGNAL_LABELS.get(signal_type, signal_type)
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f"padding:1px 8px;border-radius:9999px;font-size:12px;font-weight:500;"
        f'white-space:nowrap;color:{text_color};background:{background}">'
        f'<span style="width:6px;height:6px;border-radius:9999px;background:{dot}"></span>'
        f"{label}</span>"
    )


def empty_state(text: str, class_name: str = "") -> html.Div:
    """Muted message for sections without data."""
    return html.Div(
        text,
        className=f"text-sm text-slate-500 py-6 text-center border border-dashed border-slate-200 rounded-md {class_name}",
    )


def table(
    table_id: str, columns: list[dict], rows: list[dict], **kwargs
) -> dash_table.DataTable:
    """Dense DataTable with the shared look; extra kwargs go to DataTable."""
    css = TABLE_CSS + kwargs.pop("css", [])
    return dash_table.DataTable(
        id=table_id,
        columns=columns,
        data=rows,
        css=css,
        **TABLE_STYLE,
        **kwargs,
    )


def stat(label: str, value: object) -> html.Div:
    """Label above a value, for header cards."""
    return html.Div(
        children=[
            html.Div(label, className="text-xs text-slate-500"),
            html.Div(
                value,
                className="text-sm font-semibold text-slate-900 tabular-nums mt-0.5",
            ),
        ]
    )


def score_bar_styles(
    column_id: str, value_field: str, max_value: float, bins: int = 20
) -> list[dict]:
    """Conditional styles that draw a thin accent bar under the score.

    DataTable has no cell renderers and drops backgroundSize, so the bar is
    two gradient layers: a white cover with a transparent stripe near the
    bottom, over a horizontal value gradient that shows through it.
    """
    stripe = (
        "linear-gradient(180deg, white 0%, white 80%, transparent 80%, "
        "transparent 88%, white 88%, white 100%)"
    )
    styles = []
    for i in range(bins):
        low = max_value * i / bins
        high = max_value * (i + 1) / bins
        pct = round(100 * (i + 1) / bins)
        styles.append(
            {
                "if": {
                    "filter_query": f"{{{value_field}}} >= {low} && {{{value_field}}} < {high}"
                    if i < bins - 1
                    else f"{{{value_field}}} >= {low}",
                    "column_id": column_id,
                },
                "backgroundImage": (
                    f"{stripe}, linear-gradient(90deg, #0d9488 0%, #0d9488 {pct}%, "
                    f"#e2e8f0 {pct}%, #e2e8f0 100%)"
                ),
            }
        )
    return styles
