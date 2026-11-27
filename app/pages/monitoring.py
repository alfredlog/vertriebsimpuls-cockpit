"""Monitoring: acceptance rate, recommender metrics per run, quality checks."""

import dash
import pandas as pd
from dash import dcc, html

from app import data
from app.charts import (
    GRAPH_CONFIG,
    MODEL_COLORS,
    MODEL_ORDER,
    SIGNAL_COLORS,
    SIGNAL_ORDER,
    new_figure,
)
from app.components import card, empty_state, table
from app.format import MODEL_LABELS, SIGNAL_LABELS, fmt_date, fmt_number, fmt_pct
from app.icons import icon

dash.register_page(__name__, path="/monitoring", name="Monitoring", title="Monitoring")

# Daily list excludes known products, so "new products" @5 is the matching metric.
EVAL_GROUND_TRUTH = "new"
EVAL_K = 5
# Each card checks its own tables: after a failed first run, the quality
# results exist while schema vic does not, and they must still be shown.
FEEDBACK_TABLES = ("vic_app.impulse_feedback", "vic_app.daily_impulses")
EVAL_TABLE = "vic.reco_eval_runs"
QUALITY_TABLE = "vic_app.quality_results"


def _line(fig, x, y, name: str, color: str, hover: str) -> None:
    fig.add_scatter(
        x=x,
        y=y,
        name=name,
        mode="lines+markers",
        line={"color": color},
        marker={"color": color},
        hovertemplate=hover,
    )


def acceptance_card() -> html.Section:
    title = "Annahmequote je Signaltyp und Woche"
    if not data.has_tables(*FEEDBACK_TABLES):
        return card(title, empty_state("Noch kein Feedback erfasst."))
    rates = data.acceptance_by_week()
    if rates.empty:
        return card(title, empty_state("Noch kein Feedback erfasst."))

    weeks = [fmt_date(week) for week in sorted(rates["week"].unique())]
    fig = new_figure(height=260)
    for signal_type in SIGNAL_ORDER:
        rows = rates[rates["signal_type"] == signal_type]
        if rows.empty:
            continue
        label = SIGNAL_LABELS[signal_type]
        _line(
            fig,
            [fmt_date(week) for week in rows["week"]],
            rows["acceptance_rate"],
            label,
            SIGNAL_COLORS[signal_type][0],
            f"{label}<br>Woche ab %{{x}}: %{{y:.0%}}<extra></extra>",
        )
    fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
    # Weeks as categories: a date axis zooms to hours when there is one week.
    fig.update_xaxes(
        title_text="Woche ab (Montag)",
        type="category",
        categoryorder="array",
        categoryarray=weeks,
    )
    rows = [
        {
            "week": fmt_date(row.week),
            "signal": SIGNAL_LABELS.get(row.signal_type, row.signal_type),
            "decided": f"{int(row.accepted)} / {int(row.decided)}",
            "rate": fmt_pct(row.acceptance_rate, 0),
        }
        for row in rates.itertuples()
    ]
    columns = [
        {"id": "week", "name": "Woche ab"},
        {"id": "signal", "name": "Signaltyp"},
        {"id": "decided", "name": "angenommen / bewertet"},
        {"id": "rate", "name": "Quote"},
    ]
    return card(
        title,
        dcc.Graph(figure=fig, config=GRAPH_CONFIG),
        html.Div(
            table("acceptance-table", columns, rows, page_size=6), className="mt-2"
        ),
    )


def reco_card() -> html.Section:
    title = "Empfehlungsmodelle je Evaluationslauf"
    no_runs = not data.has_tables(EVAL_TABLE)
    runs = None if no_runs else data.reco_eval_runs(EVAL_GROUND_TRUTH, EVAL_K)
    if runs is None or runs.empty:
        return card(
            title,
            empty_state(
                "Noch kein Evaluationslauf. Start: uv run python -m vic.reco.evaluate"
            ),
        )

    fig = new_figure(height=260)
    for model in MODEL_ORDER:
        rows = runs[runs["model"] == model]
        if rows.empty:
            continue
        label = MODEL_LABELS.get(model, model)
        _line(
            fig,
            rows["run_ts"],
            rows["ndcg_at_k"],
            label,
            MODEL_COLORS[model],
            f"{label}<br>%{{x|%d.%m.%Y %H:%M}}: NDCG@{EVAL_K} %{{y:.3f}}<extra></extra>",
        )
    fig.update_yaxes(title_text=f"NDCG@{EVAL_K}", rangemode="tozero", tickformat=".2f")
    # Short ticks (runs are often on the same day); the hover shows the year.
    fig.update_xaxes(tickformat="%d.%m. %H:%M", nticks=4)

    latest = runs[runs["run_ts"] == runs["run_ts"].max()]
    best = latest["ndcg_at_k"].max()
    rows = [
        {
            "model": MODEL_LABELS.get(row.model, row.model)
            + (" (aktiv)" if row.ndcg_at_k == best else ""),
            "precision": fmt_number(row.precision_at_k, 3),
            "recall": fmt_number(row.recall_at_k, 3),
            "ndcg": fmt_number(row.ndcg_at_k, 3),
            "coverage": fmt_pct(row.coverage, 0),
        }
        for row in latest.itertuples()
    ]
    columns = [
        {"id": "model", "name": "Modell"},
        {"id": "precision", "name": f"P@{EVAL_K}"},
        {"id": "recall", "name": f"R@{EVAL_K}"},
        {"id": "ndcg", "name": f"NDCG@{EVAL_K}"},
        {"id": "coverage", "name": "Abdeckung"},
    ]
    return card(
        title,
        dcc.Graph(figure=fig, config=GRAPH_CONFIG),
        html.Div(
            f"Letzter Lauf {fmt_date(latest['run_ts'].iloc[0])} · Grundwahrheit: "
            "im Testfenster neu gehandelte Produkte · aktiv = vom Tageslauf genutzt",
            className="text-xs text-slate-500 mt-2 mb-1",
        ),
        table(
            "reco-table",
            columns,
            rows,
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "right"}
                for c in ("precision", "recall", "ndcg", "coverage")
            ],
        ),
    )


def _check_item(check: str, passed: bool, failures: int) -> html.Li:
    if passed:
        marker = html.Span(icon("check", "w-3.5 h-3.5"), className="text-green-700")
        detail = html.Span("bestanden", className="text-xs text-slate-500")
    else:
        marker = html.Span(icon("cross", "w-3.5 h-3.5"), className="text-red-700")
        detail = html.Span(
            f"{fmt_number(failures, 0)} fehlerhafte Zeilen",
            className="text-xs font-semibold text-red-700",
        )
    return html.Li(
        className="flex items-center gap-2 py-1 text-sm",
        children=[
            marker,
            html.Span(check, className="flex-1 font-mono text-xs"),
            detail,
        ],
    )


def quality_card() -> html.Section:
    title = "Datenqualitätsprüfungen (letzter Lauf)"
    if not data.has_tables(QUALITY_TABLE):
        return card(title, empty_state("Noch keine Qualitätsprüfung gelaufen."))
    results = data.latest_quality_results()
    if results.empty:
        return card(title, empty_state("Noch keine Qualitätsprüfung gelaufen."))

    n_failed = int((~results["passed"]).sum())
    summary_class = "text-green-700" if n_failed == 0 else "text-red-700"
    summary_icon = "check" if n_failed == 0 else "cross"
    summary_text = (
        f"Alle {len(results)} Prüfungen bestanden"
        if n_failed == 0
        else f"{n_failed} von {len(results)} Prüfungen fehlgeschlagen"
    )
    columns = []
    for dataset, group in results.groupby("dataset", sort=False):
        failed = group[~group["passed"]]
        passed = group[group["passed"]]
        items = [
            _check_item(r.check_name, False, r.failures) for r in failed.itertuples()
        ]
        details = html.Details(
            [
                html.Summary(
                    f"{len(passed)} bestanden",
                    className="text-xs text-slate-500 cursor-pointer select-none py-1",
                ),
                html.Ul(
                    [_check_item(r.check_name, True, 0) for r in passed.itertuples()]
                ),
            ],
            open=False,
        )
        status = (
            html.Span(icon("check", "w-4 h-4"), className="text-green-700")
            if failed.empty
            else html.Span(icon("cross", "w-4 h-4"), className="text-red-700")
        )
        columns.append(
            html.Div(
                className="rounded-md border border-slate-200 p-3",
                children=[
                    html.Div(
                        className="flex items-center gap-2 mb-1",
                        children=[
                            status,
                            html.Span(dataset, className="font-semibold text-sm"),
                            html.Span(
                                f"{len(passed)}/{len(group)}",
                                className="ml-auto text-xs tabular-nums text-slate-500",
                            ),
                        ],
                    ),
                    html.Ul(items),
                    details,
                ],
            )
        )
    run = results.iloc[0]
    return card(
        title,
        html.Div(
            className=f"flex items-center gap-2 text-sm font-semibold mb-3 {summary_class}",
            children=[
                icon(summary_icon, "w-4 h-4"),
                html.Span(summary_text),
                html.Span(
                    f"Lauf {pd.Timestamp(run.run_ts).strftime('%d.%m.%Y %H:%M')} · "
                    f"Stichtag {fmt_date(run.run_date)}",
                    className="ml-2 font-normal text-slate-500",
                ),
            ],
        ),
        html.Div(columns, className="grid grid-cols-4 gap-3"),
    )


def layout(**_kwargs) -> html.Div:
    """Page layout."""
    return html.Div(
        [
            html.Div(
                className="grid grid-cols-2 gap-4 mb-4",
                children=[acceptance_card(), reco_card()],
            ),
            quality_card(),
        ]
    )
