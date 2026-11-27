"""German labels and number formats for the UI (1.234.567 €, 3,25 %, 20.11.2026)."""

from datetime import date

import pandas as pd

from vic.signals.rules import ASSET_CLASS_LABELS_DE

SEGMENT_LABELS = {
    "cooperative_bank": "Genossenschaftsbanken",
    "third_party_bank": "Drittbanken",
    "corporate": "Firmenkunden",
    "institutional": "Institutionelle",
}
SIGNAL_LABELS = {
    "maturity": "Fälligkeit",
    "rate_move": "Zinsbewegung",
    "reactivation": "Reaktivierung",
    "concentration": "Konzentration",
    "recommendation": "Empfehlung",
}
RISK_PROFILE_LABELS = {
    "conservative": "konservativ",
    "balanced": "ausgewogen",
    "dynamic": "dynamisch",
}
MODEL_LABELS = {
    "baseline_segment_popularity": "Baseline (Segment-Popularität)",
    "item_item_cf": "Item-Item CF",
    "truncated_svd": "Truncated SVD",
}
STATUS_OPEN = "offen"
NO_VALUE = "–"


def asset_class_label(asset_class: str) -> str:
    """Return the German asset class label with a capital first letter."""
    label = ASSET_CLASS_LABELS_DE.get(asset_class, asset_class)
    return label[:1].upper() + label[1:]


def fmt_date(value: date | pd.Timestamp | None) -> str:
    """Format a date as DD.MM.YYYY; empty for missing values."""
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%d.%m.%Y")


def fmt_number(value: float, decimals: int) -> str:
    """Format a number with German separators, e.g. 1234.5 -> '1.234,5'."""
    english = f"{value:,.{decimals}f}"
    return english.replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_eur(value: float) -> str:
    """Format an EUR amount in full, e.g. 1234567 -> '1.234.567 €'."""
    return f"{fmt_number(value, 0)} €"


def fmt_eur_short(value: float) -> str:
    """Compact amount for KPI cards and chart labels, e.g. '135,2 Mio. €'."""
    if abs(value) >= 1e9:
        return f"{fmt_number(value / 1e9, 1)} Mrd. €"
    if abs(value) >= 1e6:
        return f"{fmt_number(value / 1e6, 1)} Mio. €"
    return fmt_eur(value)


def fmt_pct(share: float, decimals: int = 1) -> str:
    """Format a share (0..1) as a German percentage, e.g. 0.0325 -> '3,3 %'."""
    return f"{fmt_number(share * 100, decimals)} %"


def fmt_rate(rate_pct: float) -> str:
    """Format an interest rate given in percent, e.g. 3.25 -> '3,25 %'."""
    return f"{fmt_number(rate_pct, 2)} %"


def fmt_bp(value: float | None) -> str:
    """Format a change in basis points with sign; dash when missing."""
    if value is None or pd.isna(value):
        return NO_VALUE
    sign = "+" if value > 0 else ""
    return f"{sign}{fmt_number(value, 1)} bp"
