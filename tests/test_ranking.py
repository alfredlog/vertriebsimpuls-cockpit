from datetime import date

import pandas as pd
import pytest

from vic.ranking.score import IMPULSE_COLUMNS, rank_impulses

RUN_DATE = date(2026, 11, 20)

CLIENTS = pd.DataFrame(
    {"client_id": ["C1", "C2", "C3"], "sales_rep_id": ["R1", "R1", "R2"]}
)
PRODUCTS = pd.DataFrame(
    {
        "product_id": ["P1", "P2", "P3", "P4"],
        "name": ["Produkt 1", "Produkt 2", "Produkt 3", "Produkt 4"],
    }
)

SIGNALS = pd.DataFrame(
    [
        ("C1", "P1", "maturity", 1.0, "Fälligkeit"),
        ("C1", "P1", "concentration", 0.6, "Konzentration"),  # weaker, same pair
        ("C1", None, "reactivation", 0.8, "Reaktivierung"),  # gets C1's top reco
        ("C2", "P4", "rate_move", 0.5, "Zinsbewegung"),
    ],
    columns=["client_id", "product_id", "signal_type", "signal_strength", "reason_de"],
)
SIGNALS["facts"] = [{} for _ in range(len(SIGNALS))]

RECOMMENDATIONS = pd.DataFrame(
    [
        ("C1", "P2", 4.0, 1),  # normalized 1.0
        ("C1", "P1", 2.0, 2),  # 0.5
        ("C1", "P3", 1.0, 3),  # 0.25
        ("C2", "P2", 0.0, 1),  # best score 0 -> 0 -> dropped
        ("C3", "P3", 10.0, 1),  # 1.0
        ("C3", "P2", 5.0, 2),  # 0.5
    ],
    columns=["client_id", "product_id", "score", "rank"],
)


def ranked(top_n: int) -> pd.DataFrame:
    return rank_impulses(SIGNALS, RECOMMENDATIONS, CLIENTS, PRODUCTS, RUN_DATE, top_n)


def test_ranking_order_scores_and_reasons():
    impulses = ranked(top_n=10)

    assert list(impulses.columns) == IMPULSE_COLUMNS
    rows = impulses[
        ["sales_rep_id", "rank", "client_id", "product_id", "signal_type", "reason_de"]
    ].values.tolist()
    assert rows == [
        # 0.7 * 1.0 + 0.3 * 0.5 + 0.1 bonus (rule and reco agree) = 0.95
        ["R1", 1, "C1", "P1", "maturity", "Fälligkeit"],
        # reactivation on C1's top reco: 0.7 * 0.8 + 0.3 * 1.0, no bonus = 0.86
        ["R1", 2, "C1", "P2", "reactivation", "Reaktivierung"],
        # rule only: 0.7 * 0.5 = 0.35
        ["R1", 3, "C2", "P4", "rate_move", "Zinsbewegung"],
        # reco only: 0.3 * 0.25 = 0.075
        [
            "R1",
            4,
            "C1",
            "P3",
            "recommendation",
            "Kunden mit ähnlichem Profil handeln Produkt 3",
        ],
        [
            "R2",
            1,
            "C3",
            "P3",
            "recommendation",
            "Kunden mit ähnlichem Profil handeln Produkt 3",
        ],
        [
            "R2",
            2,
            "C3",
            "P2",
            "recommendation",
            "Kunden mit ähnlichem Profil handeln Produkt 2",
        ],
    ]
    assert impulses["score"].tolist() == pytest.approx(
        [0.95, 0.86, 0.35, 0.075, 0.3, 0.15]
    )


def test_top_n_per_sales_rep():
    impulses = ranked(top_n=2)

    assert impulses.groupby("sales_rep_id").size().to_dict() == {"R1": 2, "R2": 2}
    assert impulses["rank"].tolist() == [1, 2, 1, 2]
    assert impulses.loc[impulses["sales_rep_id"] == "R1", "product_id"].tolist() == [
        "P1",
        "P2",
    ]


def test_one_row_per_client_product_with_stable_ids():
    impulses = ranked(top_n=10)

    assert not impulses.duplicated(["client_id", "product_id"]).any()
    assert impulses.loc[0, "impulse_id"] == "2026-11-20|C1|P1"
    assert (impulses["run_date"] == RUN_DATE).all()


def test_reactivation_without_reco_keeps_empty_product():
    signals = SIGNALS[SIGNALS["signal_type"] == "reactivation"]
    no_reco = RECOMMENDATIONS[RECOMMENDATIONS["client_id"] != "C1"]
    impulses = rank_impulses(signals, no_reco, CLIENTS, PRODUCTS, RUN_DATE)

    c1 = impulses[impulses["client_id"] == "C1"]
    assert c1["product_id"].isna().tolist() == [True]
    assert c1["impulse_id"].tolist() == ["2026-11-20|C1|-"]
    assert c1["score"].tolist() == pytest.approx([0.7 * 0.8])
