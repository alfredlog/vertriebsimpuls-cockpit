"""App shell: left sidebar navigation, top bar (title, Betreuer, Stand), content.

The shell callbacks only display state (active page, title, selected sales
rep, as_of); they never write data and never feed back into page callbacks.
"""

import dash
from dash import Input, Output, callback, dcc, html

from app import data
from app.components import FOOTER_TEXT
from app.format import fmt_date
from app.icons import icon

# (key, href, label, icon, path prefix that marks the item as active)
NAV_ITEMS = (
    ("tagesimpulse", "/", "Tagesimpulse", "impulses", "/"),
    ("kunde", "/kunde/suche", "Kunde 360°", "client", "/kunde"),
    ("markt", "/markt", "Markt-Monitor", "market", "/markt"),
    ("monitoring", "/monitoring", "Monitoring", "monitoring", "/monitoring"),
)
NAV_BASE = (
    "group flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium "
    "border-l-2 transition-colors "
)
NAV_ACTIVE = NAV_BASE + "bg-slate-800 text-white border-teal-400"
NAV_INACTIVE = (
    NAV_BASE
    + "text-slate-300 border-transparent hover:bg-slate-800/60 hover:text-white"
)
ALL_REPS_LABEL = "Alle Betreuer"


def _active_key(pathname: str | None) -> str:
    path = pathname or "/"
    for key, _href, _label, _icon, prefix in NAV_ITEMS[1:]:
        if path.startswith(prefix):
            return key
    return "tagesimpulse"


def sidebar() -> html.Aside:
    links = [
        dcc.Link(
            [icon(icon_name, "w-4 h-4"), html.Span(label)],
            href=href,
            id=f"nav-{key}",
            className=NAV_INACTIVE,
        )
        for key, href, label, icon_name, _prefix in NAV_ITEMS
    ]
    return html.Aside(
        className="w-56 shrink-0 bg-slate-900 flex flex-col h-screen sticky top-0",
        children=[
            html.Div(
                className="px-5 py-4 border-b border-slate-800",
                children=[
                    html.Div("Vertriebsimpuls", className="text-white font-semibold"),
                    html.Div(
                        "Cockpit · Kapitalmarkt", className="text-xs text-slate-400"
                    ),
                ],
            ),
            html.Nav(className="flex-1 px-3 py-4 flex flex-col gap-1", children=links),
        ],
    )


def topbar() -> html.Header:
    return html.Header(
        className="h-14 bg-white border-b border-slate-200 px-6 flex items-center "
        "justify-between gap-4 sticky top-0 z-10",
        children=[
            html.H1(
                id="topbar-title", className="text-lg font-semibold text-slate-900"
            ),
            html.Div(
                className="flex items-center gap-4 text-sm",
                children=[
                    html.Div(
                        className="flex items-center gap-2 text-slate-600",
                        children=[
                            html.Span("Betreuer:", className="text-slate-500"),
                            html.Span(
                                ALL_REPS_LABEL,
                                id="topbar-rep",
                                className="font-medium text-slate-900",
                            ),
                        ],
                    ),
                    html.Div(
                        className="flex items-center gap-2 px-2.5 py-1 rounded-md "
                        "bg-slate-100 text-slate-700",
                        children=[
                            icon("calendar", "w-4 h-4"),
                            html.Span(
                                id="topbar-stand", className="font-medium tabular-nums"
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def shell(content: object) -> html.Div:
    """Full page frame around the Dash page container."""
    return html.Div(
        className="flex min-h-screen bg-slate-50 text-slate-900 antialiased",
        style={"minWidth": "1280px"},
        children=[
            dcc.Location(id="url", refresh="callback-nav"),
            dcc.Store(id="selected-rep", storage_type="session"),
            sidebar(),
            html.Div(
                className="flex-1 min-w-0 flex flex-col",
                children=[
                    topbar(),
                    html.Main(className="flex-1 px-6 py-5", children=content),
                    html.Footer(
                        FOOTER_TEXT,
                        className="px-6 py-3 text-xs text-slate-500 border-t border-slate-200",
                    ),
                ],
            ),
        ],
    )


@callback(
    Output("topbar-title", "children"),
    Output("topbar-rep", "children"),
    Output("topbar-stand", "children"),
    *[Output(f"nav-{key}", "className") for key, *_ in NAV_ITEMS],
    Input("url", "pathname"),
    Input("selected-rep", "data"),
)
def update_shell(pathname: str | None, rep_id: str | None):
    """Show the active page, its title, the selected sales rep and the Stand."""
    active = _active_key(pathname)
    title = next(label for key, _h, label, _i, _p in NAV_ITEMS if key == active)

    rep_name = ALL_REPS_LABEL
    if rep_id and data.has_impulses():
        reps = data.sales_reps().set_index("sales_rep_id")["name"]
        rep_name = reps.get(rep_id, ALL_REPS_LABEL)

    run_date = data.latest_run_date() if data.has_impulses() else None
    stand = f"Stand: {fmt_date(run_date)}" if run_date else "Stand: –"
    classes = [NAV_ACTIVE if key == active else NAV_INACTIVE for key, *_ in NAV_ITEMS]
    return (title, rep_name, stand, *classes)


@callback(Output("selected-rep", "data"), Input("filter-rep", "value"))
def remember_rep(rep_id: str | None):
    """Keep the rep chosen on Tagesimpulse for the top bar of every page."""
    return rep_id


@callback(
    Output("url", "pathname"),
    Input("client-picker", "value"),
    prevent_initial_call=True,
)
def open_client(client_id: str | None):
    """Navigate to Kunde 360° of the picked client."""
    if not client_id:
        return dash.no_update
    return f"/kunde/{client_id}"
