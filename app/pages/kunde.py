"""Kunde 360°: header, today's impulses, trade history + product mix, positions."""

import dash
import pandas as pd
from dash import dcc, html

from app import data
from app.charts import (
    ASSET_GROUP_COLORS,
    ASSET_GROUP_ORDER,
    ASSET_GROUPS,
    GRAPH_CONFIG,
    INK,
    SURFACE,
    new_figure,
)
from app.components import (
    NO_DATA_TEXT,
    card,
    chip,
    empty_state,
    signal_chip,
    stat,
    table,
)
from app.format import (
    NO_VALUE,
    RISK_PROFILE_LABELS,
    SEGMENT_LABELS,
    fmt_date,
    fmt_eur,
    fmt_eur_short,
    fmt_number,
    fmt_pct,
)
from vic.signals.rules import MATURITY_WINDOW_DAYS

dash.register_page(
    __name__, path_template="/kunde/<client_id>", name="Kunde 360°", title="Kunde 360°"
)

RECENT_TRADES = 10
HISTORY_MONTHS = 24
MIX_MONTHS = 12
TOP_CLIENTS = 12
# /kunde/suche is the client selection (the sidebar entry); it never clashes
# with a client id, which always starts with "C".
SEARCH_SLUG = "suche"


def client_picker(value: str | None = None) -> dcc.Dropdown:
    """Searchable client selection; the shell callback navigates on change."""
    options = [
        {
            "label": f"{row.name} · {SEGMENT_LABELS.get(row.segment, row.segment)}",
            "value": row.client_id,
        }
        for row in data.client_options().itertuples()
    ]
    return dcc.Dropdown(
        id="client-picker",
        options=options,
        value=value,
        placeholder="Kunde suchen …",
        clearable=False,
        className="w-80 text-sm",
    )


def header_card(client: pd.Series) -> html.Section:
    risk = RISK_PROFILE_LABELS.get(client.risk_profile, client.risk_profile)
    chips = [
        chip(SEGMENT_LABELS.get(client.segment, client.segment)),
        chip(f"Risikoprofil {risk}"),
    ]
    if client.has_floating_rate_debt:
        chips.append(chip("variable Finanzierung", "#065f46", "#ecfdf5"))
    return card(
        None,
        html.Div(
            className="flex items-start justify-between gap-6",
            children=[
                html.Div(
                    [
                        html.Div(
                            client["name"],
                            className="text-xl font-semibold text-slate-900",
                        ),
                        html.Div(chips, className="flex flex-wrap gap-2 mt-2"),
                    ]
                ),
                client_picker(client.client_id),
            ],
        ),
        html.H3("Stammdaten", className="sr-only"),
        html.Div(
            className="grid grid-cols-4 gap-6 mt-4 pt-4 border-t border-slate-100",
            children=[
                stat("Verwaltetes Vermögen", fmt_eur(client.aum_eur)),
                stat("Betreuer", client.rep_name),
                stat("Region", client.region),
                stat("Kunden-ID", client.client_id),
            ],
        ),
        class_name="mb-4",
    )


def impulses_list(client_id: str) -> html.Div:
    impulses = data.client_impulses(client_id)
    if impulses.empty:
        return empty_state("Heute keine Impulse für diesen Kunden.")
    rows = [
        html.Div(
            className="flex items-start gap-3 py-2 border-b border-slate-100 "
            "last:border-0",
            children=[
                html.Div(signal_chip(row.signal_type), className="w-32 shrink-0"),
                html.Div(
                    row.product_name or NO_VALUE,
                    className="w-44 shrink-0 text-sm font-medium",
                ),
                html.Div(row.reason_de, className="flex-1 text-sm text-slate-700"),
                html.Div(
                    fmt_number(row.score, 2),
                    className="w-12 text-right text-sm font-semibold tabular-nums",
                ),
            ],
        )
        for row in impulses.itertuples()
    ]
    return html.Div(rows)


def _monthly_by_group(client_id: str) -> pd.DataFrame:
    monthly = data.client_monthly_notional(client_id)
    if monthly.empty:
        return monthly
    monthly["group"] = monthly["asset_class"].map(ASSET_GROUPS)
    monthly["month"] = pd.to_datetime(monthly["month"])
    return monthly.groupby(["month", "group"], as_index=False)["notional_eur"].sum()


def history_figure(monthly: pd.DataFrame):
    """Stacked monthly volume per asset group over the last 24 months."""
    last = monthly["month"].max()
    recent = monthly[monthly["month"] > last - pd.DateOffset(months=HISTORY_MONTHS)]
    fig = new_figure(height=300)
    for group in ASSET_GROUP_ORDER:
        rows = recent[recent["group"] == group]
        if rows.empty:
            continue
        fig.add_bar(
            x=rows["month"],
            y=rows["notional_eur"] / 1e6,
            name=group,
            marker_color=ASSET_GROUP_COLORS[group],
            hovertemplate=f"{group}<br>%{{x|%m.%Y}}: %{{y:,.1f}} Mio. €<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    fig.update_yaxes(title_text="Mio. €")
    fig.update_xaxes(tickformat="%m.%Y", dtick="M3")
    return fig


def product_mix(monthly: pd.DataFrame) -> html.Div:
    """Donut of the last 12 months by asset group, with a value list as table view."""
    last = monthly["month"].max()
    window = monthly[monthly["month"] > last - pd.DateOffset(months=MIX_MONTHS)]
    mix = window.groupby("group")["notional_eur"].sum()
    mix = mix.reindex([g for g in ASSET_GROUP_ORDER if g in mix.index])
    if mix.empty:
        return empty_state("Keine Geschäfte in den letzten 12 Monaten.")
    total = mix.sum()

    fig = new_figure(height=200)
    fig.add_pie(
        labels=mix.index,
        values=mix.values,
        hole=0.68,
        sort=False,
        direction="clockwise",
        marker={
            "colors": [ASSET_GROUP_COLORS[g] for g in mix.index],
            "line": {"color": SURFACE, "width": 2},
        },
        textinfo="none",
        hovertemplate="%{label}<br>%{value:,.0f} €<br>%{percent}<extra></extra>",
    )
    fig.update_layout(
        showlegend=False,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        annotations=[
            {
                "text": f"<b>{fmt_eur_short(total)}</b><br>12 Monate",
                "showarrow": False,
                "font": {"color": INK, "size": 13},
            }
        ],
    )
    legend = [
        html.Div(
            className="flex items-center gap-2 text-sm py-1",
            children=[
                html.Span(
                    className="inline-block w-2.5 h-2.5 rounded-sm",
                    style={"backgroundColor": ASSET_GROUP_COLORS[group]},
                ),
                html.Span(group, className="flex-1 text-slate-700"),
                html.Span(
                    fmt_pct(value / total, 0), className="tabular-nums font-medium"
                ),
            ],
        )
        for group, value in mix.items()
    ]
    return html.Div(
        [dcc.Graph(figure=fig, config=GRAPH_CONFIG), html.Div(legend, className="mt-2")]
    )


def positions_table(client_id: str):
    positions = data.client_open_positions(client_id)
    if positions.empty:
        return empty_state("Keine offenen Positionen.")
    rows = [
        {
            "product": row.product_name,
            "notional": fmt_eur(row.notional_eur),
            "maturity": fmt_date(row.maturity_date),
            "days": f"{fmt_number(row.days_to_maturity, 0)} "
            + ("Tag" if row.days_to_maturity == 1 else "Tage"),
            # Unformatted copy for the highlight rule; "1.249 Tage" is a string.
            "days_value": int(row.days_to_maturity),
        }
        for row in positions.itertuples()
    ]
    columns = [
        {"id": "product", "name": "Produkt"},
        {"id": "notional", "name": "Volumen"},
        {"id": "maturity", "name": "Fälligkeit"},
        {"id": "days", "name": "Restlaufzeit"},
    ]
    soon = f"{{days_value}} <= {MATURITY_WINDOW_DAYS}"
    return table(
        "client-positions",
        columns,
        rows,
        page_size=8,
        style_cell_conditional=[
            {"if": {"column_id": c}, "textAlign": "right"} for c in ("notional", "days")
        ],
        style_data_conditional=[
            {"if": {"filter_query": soon}, "backgroundColor": "#fffbeb"},
            {
                "if": {"filter_query": soon, "column_id": ["maturity", "days"]},
                "color": "#78350f",
                "fontWeight": "700",
            },
        ],
    )


def recent_trades_table(client_id: str):
    trades = data.client_recent_trades(client_id, RECENT_TRADES)
    if trades.empty:
        return empty_state("Keine Geschäfte.")
    rows = [
        {
            "date": fmt_date(row.trade_date),
            "product": row.product_name,
            "notional": fmt_eur(row.notional_eur),
        }
        for row in trades.itertuples()
    ]
    columns = [
        {"id": "date", "name": "Datum"},
        {"id": "product", "name": "Produkt"},
        {"id": "notional", "name": "Volumen"},
    ]
    return table(
        "client-trades",
        columns,
        rows,
        style_cell_conditional=[
            {"if": {"column_id": "notional"}, "textAlign": "right"}
        ],
    )


def top_clients() -> html.Div:
    """Clients with the strongest impulse today, as quick links."""
    if not data.has_impulses():
        return empty_state(NO_DATA_TEXT)
    impulses = data.impulses(data.latest_run_date())
    best = (
        impulses.sort_values("score", ascending=False)
        .drop_duplicates("client_id")
        .head(TOP_CLIENTS)
    )
    tiles = [
        dcc.Link(
            href=f"/kunde/{row.client_id}",
            className="block rounded-md border border-slate-200 p-3 "
            "hover:border-teal-600 hover:bg-teal-50 transition-colors",
            children=[
                html.Div(
                    className="flex items-center justify-between gap-2",
                    children=[
                        html.Span(
                            row.client_name, className="font-medium text-sm truncate"
                        ),
                        html.Span(
                            fmt_number(row.score, 2),
                            className="text-xs font-semibold tabular-nums text-slate-600",
                        ),
                    ],
                ),
                html.Div(
                    className="flex items-center gap-2 mt-1.5",
                    children=[
                        signal_chip(row.signal_type),
                        html.Span(
                            SEGMENT_LABELS.get(row.segment, row.segment),
                            className="text-xs text-slate-500",
                        ),
                    ],
                ),
            ],
        )
        for row in best.itertuples()
    ]
    return html.Div(tiles, className="grid grid-cols-3 gap-3")


def search_layout(message: str | None = None) -> html.Div:
    """Client selection: picker plus today's strongest clients."""
    first = [client_picker()]
    if message:
        first.insert(0, empty_state(message, class_name="mb-3"))
    return html.Div(
        [
            card("Kunde auswählen", *first, class_name="mb-4"),
            card("Kunden mit den stärksten Impulsen heute", top_clients()),
        ]
    )


def layout(client_id: str | None = None, **_kwargs) -> html.Div:
    """Page layout for one client, or the client selection at /kunde/suche."""
    if client_id in (None, SEARCH_SLUG):
        return search_layout()
    client = data.client(client_id)
    if client is None:
        return search_layout("Kunde nicht gefunden.")

    monthly = _monthly_by_group(client.client_id)
    if monthly.empty:
        history = empty_state("Keine Geschäfte im Betrachtungszeitraum.")
        mix = empty_state("Keine Geschäfte im Betrachtungszeitraum.")
    else:
        history = dcc.Graph(figure=history_figure(monthly), config=GRAPH_CONFIG)
        mix = product_mix(monthly)

    soon_hint = html.Span(
        f"≤ {MATURITY_WINDOW_DAYS} Tage hervorgehoben",
        className="text-xs text-amber-900 bg-amber-50 px-2 py-0.5 rounded",
    )
    return html.Div(
        [
            header_card(client),
            card(
                "Impulse und Empfehlungen heute",
                impulses_list(client.client_id),
                class_name="mb-4",
            ),
            html.Div(
                className="grid grid-cols-3 gap-4 mb-4",
                children=[
                    card(
                        "Geschäftshistorie (Volumen je Monat)",
                        history,
                        class_name="col-span-2",
                    ),
                    card("Produktmix (12 Monate)", mix),
                ],
            ),
            html.Div(
                className="grid grid-cols-2 gap-4",
                children=[
                    card(
                        "Offene Positionen",
                        positions_table(client.client_id),
                        right=soon_hint,
                    ),
                    card("Letzte Geschäfte", recent_trades_table(client.client_id)),
                ],
            ),
        ]
    )
