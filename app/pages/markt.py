"""Markt-Monitor: AAA yield curve today vs one month ago, bp change cards."""

import dash
import pandas as pd
from dash import dcc, html

from app import data
from app.charts import ACCENT, GRAPH_CONFIG, INK, REFERENCE, new_figure
from app.components import NO_DATA_TEXT, card, chip, empty_state
from app.format import NO_VALUE, fmt_bp, fmt_date, fmt_number, fmt_rate
from app.icons import icon
from vic.signals.rules import RATE_MOVE_TENORS, RATE_MOVE_THRESHOLD_BP

dash.register_page(__name__, path="/markt", name="Markt-Monitor", title="Markt-Monitor")

REQUIRED_TABLES = ("vic.market_rates", "vic.run_context")

# Today carries the accent; the reference curve is muted and dashed.
# label_shift keeps the two end labels apart when the curves almost touch.
CURVE_STYLES = {
    "aktuell": {"color": ACCENT, "dash": "solid", "label_shift": -9},
    "vor 1 Monat": {"color": REFERENCE, "dash": "dash", "label_shift": 9},
}


def curve_figure(curves: pd.DataFrame):
    """Spot rate per tenor for both dates, direct-labelled at the 30Y end."""
    fig = new_figure(height=320)
    for curve, style in CURVE_STYLES.items():
        rows = curves[curves["curve"] == curve]
        if rows.empty:
            continue
        label = f"{curve} ({fmt_date(rows['rate_date'].iloc[0])})"
        fig.add_scatter(
            x=rows["tenor_years"],
            y=rows["rate_pct"],
            name=label,
            mode="lines+markers",
            line={"color": style["color"], "dash": style["dash"]},
            marker={"color": style["color"]},
            hovertemplate=f"{label}<br>%{{x}}J: %{{y:.2f}} %<extra></extra>",
        )
        last = rows.iloc[-1]
        fig.add_annotation(
            x=last["tenor_years"],
            y=last["rate_pct"],
            text=curve,
            xanchor="left",
            xshift=10,
            yshift=style["label_shift"],
            showarrow=False,
            font={"color": INK, "size": 12},
        )
    fig.update_xaxes(
        title_text="Laufzeit (Jahre)",
        tickvals=sorted(curves["tenor_years"].unique()),
        ticksuffix="J",
    )
    fig.update_yaxes(title_text="Kassazins", ticksuffix=" %", tickformat=".2f")
    fig.update_layout(margin={"r": 90})
    return fig


def change_card(row) -> html.Div:
    """One tenor: rate, 14-day change (colored, with arrow) and 1-month change."""
    change = row.change_14d_bp
    watched = int(row.tenor_years) in RATE_MOVE_TENORS
    fired = watched and pd.notna(change) and abs(change) > RATE_MOVE_THRESHOLD_BP
    if pd.isna(change) or change == 0:
        tone, arrow = "text-slate-700", []
    elif change > 0:
        tone, arrow = "text-green-700", [icon("arrow_up", "w-4 h-4")]
    else:
        tone, arrow = "text-red-700", [icon("arrow_down", "w-4 h-4")]
    border = "border-teal-600 ring-1 ring-teal-600" if fired else "border-slate-200"
    badges = [chip("Signal", "#134e4a", "#ccfbf1")] if fired else []
    if watched and not fired:
        badges = [chip("beobachtet")]
    return html.Div(
        className=f"bg-white rounded-lg border {border} shadow-sm px-4 py-3",
        children=[
            html.Div(
                className="flex items-center justify-between",
                children=[
                    html.Span(
                        f"{fmt_number(row.tenor_years, 0)} "
                        + ("Jahr" if int(row.tenor_years) == 1 else "Jahre"),
                        className="text-xs font-medium uppercase tracking-wide "
                        "text-slate-500",
                    ),
                    *badges,
                ],
            ),
            html.Div(
                fmt_rate(row.rate_pct),
                className="text-xl font-semibold tabular-nums text-slate-900 mt-1",
            ),
            html.Div(
                [
                    *arrow,
                    html.Span(fmt_bp(change)),
                    html.Span("14 Tage", className="text-slate-500 font-normal ml-1"),
                ],
                className=f"flex items-center gap-1 text-sm font-semibold tabular-nums {tone}",
            ),
            html.Div(
                f"1 Monat: {fmt_bp(row.change_1m_bp)}",
                className="text-xs text-slate-500 tabular-nums mt-0.5",
            ),
        ],
    )


def layout(**_kwargs) -> html.Div:
    """Page layout."""
    if not data.has_tables(*REQUIRED_TABLES):
        return card("Zinsstrukturkurve", empty_state(NO_DATA_TEXT))
    curves = data.yield_curves()
    changes = data.rate_changes()
    if curves.empty or changes.empty:
        return card("Zinsstrukturkurve", empty_state("Keine Marktdaten geladen."))

    tenors = " und ".join(f"{t}J" for t in RATE_MOVE_TENORS)
    stand = fmt_date(changes["rate_date"].max()) if not changes.empty else NO_VALUE
    return html.Div(
        [
            html.Div(
                className="grid grid-cols-5 gap-4 mb-4",
                children=[change_card(row) for row in changes.itertuples()],
            ),
            card(
                "Zinsstrukturkurve: aktuell und vor einem Monat",
                dcc.Graph(figure=curve_figure(curves), config=GRAPH_CONFIG),
                html.P(
                    f"Euroraum, Staatsanleihen AAA, Kassazinsen (Quelle: EZB), Stand "
                    f"{stand}. Signal „Zinsbewegung“: mehr als "
                    f"±{fmt_number(RATE_MOVE_THRESHOLD_BP, 0)} bp in 14 Tagen bei "
                    f"{tenors}; ausgelöste Laufzeiten sind oben markiert.",
                    className="text-xs text-slate-500 mt-2",
                ),
                right=html.Span(
                    "Veränderungen in Basispunkten oben",
                    className="text-xs text-slate-500",
                ),
            ),
        ]
    )
