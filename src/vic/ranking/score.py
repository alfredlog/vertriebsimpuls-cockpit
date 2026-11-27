"""Combine rule signals and recommender scores into one ranked list per sales rep.

score = W_RULE * signal_strength + W_RECO * normalized_reco_score
        (+ AGREEMENT_BONUS when a rule and the recommender name the same product).
Why the weights look like this: docs/adr/0003-score-weighting.md.
"""

from datetime import date

import pandas as pd

W_RULE = 0.7
W_RECO = 0.3
AGREEMENT_BONUS = 0.1
TOP_N = 10

SIGNAL_TYPE_RECOMMENDATION = "recommendation"
RECO_REASON_TEMPLATE = "Kunden mit ähnlichem Profil handeln {product_name}"
NO_PRODUCT_ID = "-"

IMPULSE_COLUMNS = [
    "impulse_id",
    "run_date",
    "sales_rep_id",
    "client_id",
    "product_id",
    "signal_type",
    "score",
    "rank",
    "reason_de",
]


def normalize_reco_scores(recommendations: pd.DataFrame) -> pd.DataFrame:
    """Scale each client's scores to 0..1 by dividing by the client's best score.

    Models have different score scales; per client the best candidate gets 1.
    A client whose best score is <= 0 gets 0 everywhere.
    """
    reco = recommendations[["client_id", "product_id", "score", "rank"]].copy()
    best = reco.groupby("client_id")["score"].transform("max")
    reco["reco_score"] = (reco["score"] / best).where(best > 0, 0.0)
    return reco


def attach_products_to_signals(
    signals: pd.DataFrame, reco: pd.DataFrame
) -> pd.DataFrame:
    """Give product-less signals (reactivation) the client's top recommended product.

    The rule says *why now*, the recommender says *what*. Such pairs get no
    agreement bonus because the recommender chose the product.
    """
    signals = signals.copy()
    top_product = reco[reco["rank"] == 1].set_index("client_id")["product_id"]
    missing = signals["product_id"].isna()
    signals["product_from_reco"] = missing & signals["client_id"].isin(
        top_product.index
    )
    signals.loc[missing, "product_id"] = signals.loc[missing, "client_id"].map(
        top_product
    )
    return signals


def strongest_signal_per_pair(signals: pd.DataFrame) -> pd.DataFrame:
    """Keep one signal per client-product: the strongest one, with its reason."""
    ordered = signals.sort_values(
        ["client_id", "product_id", "signal_strength", "signal_type"],
        ascending=[True, True, False, True],
        na_position="last",
    )
    return ordered.drop_duplicates(["client_id", "product_id"], keep="first")


def combine_scores(
    signals: pd.DataFrame, reco: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    """Return one scored row per client-product with signal_type and reason_de."""
    pairs = strongest_signal_per_pair(attach_products_to_signals(signals, reco))
    combined = pairs[
        [
            "client_id",
            "product_id",
            "signal_type",
            "signal_strength",
            "reason_de",
            "product_from_reco",
        ]
    ].merge(
        reco[["client_id", "product_id", "reco_score"]],
        on=["client_id", "product_id"],
        how="outer",
    )

    has_signal = combined["signal_strength"].notna()
    has_reco = combined["reco_score"].notna() & (combined["reco_score"] > 0)
    agree = has_signal & has_reco & ~combined["product_from_reco"].eq(True)
    combined["score"] = (
        W_RULE * combined["signal_strength"].fillna(0.0)
        + W_RECO * combined["reco_score"].fillna(0.0)
        + AGREEMENT_BONUS * agree
    )

    names = products.set_index("product_id")["name"]
    reco_reasons = (
        combined["product_id"]
        .map(names)
        .map(lambda name: RECO_REASON_TEMPLATE.format(product_name=name))
    )
    combined["reason_de"] = combined["reason_de"].where(has_signal, reco_reasons)
    combined["signal_type"] = combined["signal_type"].where(
        has_signal, SIGNAL_TYPE_RECOMMENDATION
    )
    return combined[combined["score"] > 0]


def rank_impulses(
    signals: pd.DataFrame,
    recommendations: pd.DataFrame,
    clients: pd.DataFrame,
    products: pd.DataFrame,
    run_date: date,
    top_n: int = TOP_N,
) -> pd.DataFrame:
    """Return the top_n impulses per sales rep, ranked by combined score.

    Ties are broken by client_id and product_id so the ranking is reproducible.
    """
    reco = normalize_reco_scores(recommendations)
    combined = combine_scores(signals, reco, products)
    combined = combined.merge(clients[["client_id", "sales_rep_id"]], on="client_id")

    combined = combined.sort_values(
        ["sales_rep_id", "score", "client_id", "product_id"],
        ascending=[True, False, True, True],
        na_position="last",
    )
    top = combined.groupby("sales_rep_id").head(top_n).copy()
    top["rank"] = top.groupby("sales_rep_id").cumcount() + 1
    top["run_date"] = run_date
    product_key = top["product_id"].fillna(NO_PRODUCT_ID)
    top["impulse_id"] = (
        run_date.isoformat() + "|" + top["client_id"] + "|" + product_key
    )
    return top[IMPULSE_COLUMNS].reset_index(drop=True)
