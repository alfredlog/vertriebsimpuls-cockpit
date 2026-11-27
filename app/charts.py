"""One shared Plotly template and the fixed colors of every data category.

UI chrome uses neutral slate plus ONE accent (teal). Charts use a separate
categorical palette without blue or orange, validated with the data-viz
validator (light surface, adjacent pairs: CVD dE >= 9.1, normal >= 19.6).
Colors follow the entity, never its rank: a signal type, asset group or model
has the same color on every page and in every chip.
"""

import plotly.graph_objects as go
import plotly.io as pio

FONT_FAMILY = (
    "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
)

# Neutral slate scale (Tailwind slate) and the accent.
INK = "#0f172a"  # slate-900
INK_SECONDARY = "#475569"  # slate-600
INK_MUTED = "#64748b"  # slate-500, 4.8:1 on white (AA)
GRID = "#e2e8f0"  # slate-200
AXIS = "#cbd5e1"  # slate-300
SURFACE = "#ffffff"
ACCENT = "#0f766e"  # teal-700, 5.4:1 on white (AA)
REFERENCE = "#94a3b8"  # slate-400, for "before" series next to the accent

# Validated categorical order: violet, aqua, amber, magenta, green.
VIOLET = "#6250d6"
AQUA = "#1baf7a"
AMBER = "#eda100"
MAGENTA = "#e87ba4"
GREEN = "#008300"
CATEGORICAL = (VIOLET, AQUA, AMBER, MAGENTA, GREEN)

STATUS_GOOD = "#15803d"  # green-700, text-safe
STATUS_BAD = "#b91c1c"  # red-700, text-safe

LINE_WIDTH = 2
MARKER_SIZE = 8
BAR_GAP_PX = 2
BAR_CORNER_RADIUS = 4

# Signal types: chart color, chip text color, chip background (text >= 4.5:1).
SIGNAL_COLORS = {
    "maturity": (VIOLET, "#4c1d95", "#f5f3ff"),
    "rate_move": (AQUA, "#065f46", "#ecfdf5"),
    "reactivation": (AMBER, "#78350f", "#fffbeb"),
    "concentration": (MAGENTA, "#831843", "#fdf2f8"),
    "recommendation": (INK_MUTED, "#334155", "#f1f5f9"),
}
SIGNAL_ORDER = tuple(SIGNAL_COLORS)

# Eight asset classes folded into five groups: five colors stay distinguishable.
ASSET_GROUPS = {
    "government_bond": "Staatsanleihen & Pfandbriefe",
    "covered_bond": "Staatsanleihen & Pfandbriefe",
    "corporate_bond": "Unternehmensanleihen",
    "rates_derivative": "Zins- & Devisenderivate",
    "fx_derivative": "Zins- & Devisenderivate",
    "money_market": "Geldmarkt",
    "structured_product": "Fonds & Strukturierte",
    "fund": "Fonds & Strukturierte",
}
ASSET_GROUP_ORDER = (
    "Staatsanleihen & Pfandbriefe",
    "Unternehmensanleihen",
    "Zins- & Devisenderivate",
    "Geldmarkt",
    "Fonds & Strukturierte",
)
ASSET_GROUP_COLORS = dict(zip(ASSET_GROUP_ORDER, CATEGORICAL, strict=True))

MODEL_ORDER = ("baseline_segment_popularity", "item_item_cf", "truncated_svd")
MODEL_COLORS = dict(zip(MODEL_ORDER, CATEGORICAL, strict=False))

TEMPLATE_NAME = "vic"

pio.templates[TEMPLATE_NAME] = go.layout.Template(
    layout={
        "font": {"family": FONT_FAMILY, "color": INK_SECONDARY, "size": 12},
        "paper_bgcolor": SURFACE,
        "plot_bgcolor": SURFACE,
        "colorway": list(CATEGORICAL),
        "separators": ",.",
        "margin": {"l": 56, "r": 16, "t": 8, "b": 40},
        "hovermode": "closest",
        "hoverlabel": {
            "bgcolor": SURFACE,
            "bordercolor": AXIS,
            "font": {"family": FONT_FAMILY, "color": INK, "size": 12},
        },
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"color": INK, "size": 12},
            "itemclick": "toggleothers",
        },
        "xaxis": {
            "showgrid": False,
            "linecolor": AXIS,
            "ticks": "outside",
            "tickcolor": AXIS,
            "tickfont": {"color": INK_MUTED},
            "title": {"font": {"color": INK_SECONDARY, "size": 12}},
            "zeroline": False,
            "automargin": True,
            "tickangle": 0,
        },
        "yaxis": {
            "showgrid": True,
            "gridcolor": GRID,
            "gridwidth": 1,
            "showline": False,
            "tickfont": {"color": INK_MUTED},
            "title": {"font": {"color": INK_SECONDARY, "size": 12}, "standoff": 12},
            "zeroline": False,
            "automargin": True,
        },
        "barcornerradius": BAR_CORNER_RADIUS,
        "bargap": 0.3,
    },
    data={
        "scatter": [
            go.Scatter(line={"width": LINE_WIDTH}, marker={"size": MARKER_SIZE})
        ],
        "bar": [go.Bar(marker={"line": {"color": SURFACE, "width": BAR_GAP_PX}})],
    },
)


def new_figure(height: int = 300) -> go.Figure:
    """Return an empty figure that uses the shared template."""
    return go.Figure(layout={"template": TEMPLATE_NAME, "height": height})


GRAPH_CONFIG = {"displayModeBar": False}
