import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import get_connection, load_all
from vic.reco.baseline import SegmentPopularity
from vic.reco.evaluate import (
    catalog_coverage,
    ground_truth,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    run,
    split_date,
)
from vic.reco.interactions import RECO_COLUMNS, build_interactions
from vic.reco.item_cf import ItemItemCF, cosine_similarity
from vic.reco.svd import SVDRecommender

AS_OF = date(2026, 6, 30)  # split T = 2026-04-01

# --- metrics -----------------------------------------------------------------

RECOMMENDED = ["A", "B", "C", "D", "E"]
RELEVANT = {"B", "E", "X"}  # hits at rank 2 and 5; X is never recommended


def test_precision_and_recall_by_hand():
    assert precision_at_k(RECOMMENDED, RELEVANT, 3) == pytest.approx(1 / 3)
    assert recall_at_k(RECOMMENDED, RELEVANT, 3) == pytest.approx(1 / 3)
    assert precision_at_k(RECOMMENDED, RELEVANT, 5) == pytest.approx(2 / 5)
    assert recall_at_k(RECOMMENDED, RELEVANT, 5) == pytest.approx(2 / 3)


def test_ndcg_by_hand():
    # DCG@3 = 1/log2(3); IDCG (3 relevant) = 1 + 1/log2(3) + 1/log2(4)
    assert ndcg_at_k(RECOMMENDED, RELEVANT, 3) == pytest.approx(0.2960819109658652)
    # DCG@5 = 1/log2(3) + 1/log2(6); same IDCG
    assert ndcg_at_k(RECOMMENDED, RELEVANT, 5) == pytest.approx(0.4776237035032179)


def test_ndcg_extremes():
    assert ndcg_at_k(["B", "E", "A"], {"B", "E"}, 3) == pytest.approx(1.0)
    assert ndcg_at_k(["A", "C", "D"], {"B"}, 3) == 0.0


def test_catalog_coverage_by_hand():
    assert catalog_coverage([["A", "B"], ["B", "C"]], 4) == pytest.approx(0.75)


# --- cosine similarity -------------------------------------------------------


def test_cosine_similarity_on_3x3():
    # Columns: p0 = (2, 0, 0), p1 = (1, 1, 0), p2 = (0, 0, 3)
    matrix = sparse.csr_matrix([[2, 1, 0], [0, 1, 0], [0, 0, 3]])
    similarity = cosine_similarity(matrix)

    # cos(p0, p1) = 2 / (2 * sqrt(2)); p2 is orthogonal; diagonal set to 0
    s = 1 / math.sqrt(2)
    assert similarity == pytest.approx(np.array([[0, s, 0], [s, 0, 0], [0, 0, 0]]))


def test_cosine_similarity_of_untraded_product_is_zero():
    matrix = sparse.csr_matrix([[1, 0], [1, 0]])
    assert cosine_similarity(matrix) == pytest.approx(np.zeros((2, 2)))


# --- interactions and split --------------------------------------------------

TRADES = pd.DataFrame(
    [
        ("C1", "P1", "2026-01-15"),
        ("C1", "P1", "2026-03-31"),  # last train day
        ("C1", "P2", "2026-04-01"),  # first test day (T)
        ("C1", "P1", "2026-05-01"),  # repeat business in test
        ("C2", "P1", "2026-06-30"),  # as_of, included in test
        ("C3", "P2", "2026-02-01"),  # train only
        ("C3", "P2", "2026-07-01"),  # after as_of, ignored
    ],
    columns=["client_id", "product_id", "trade_date"],
)
CLIENTS = ["C1", "C2", "C3"]
PRODUCTS = ["P1", "P2"]


def test_interaction_values_are_log1p_trade_counts():
    train = build_interactions(TRADES, CLIENTS, PRODUCTS, split_date(AS_OF))

    expected = np.array([[math.log1p(2), 0], [0, 0], [0, math.log1p(1)]])
    assert train.matrix.toarray() == pytest.approx(expected)
    assert train.client_index == {"C1": 0, "C2": 1, "C3": 2}
    assert train.product_index == {"P1": 0, "P2": 1}


def test_ground_truth_new_and_all():
    new, all_products = ground_truth(TRADES, AS_OF)

    assert all_products == {"C1": {"P1", "P2"}, "C2": {"P1"}}
    # C1 already traded P1 in train; C3 has no test trade and is excluded.
    assert new == {"C1": {"P2"}, "C2": {"P1"}}


@pytest.fixture(scope="module")
def generated():
    data = generate_all(seed=42, as_of=AS_OF_DEFAULT)
    clients = data["clients"]
    product_ids = data["products"]["product_id"].tolist()
    train = build_interactions(
        data["trades"],
        clients["client_id"].tolist(),
        product_ids,
        split_date(AS_OF_DEFAULT),
    )
    return data, train


def test_no_test_window_trade_in_training_matrix(generated):
    data, train = generated
    trades = data["trades"]
    cutoff = pd.Timestamp(split_date(AS_OF_DEFAULT))
    before = trades[trades["trade_date"] < cutoff]
    expected_counts = before.groupby(["client_id", "product_id"]).size()

    coo = train.matrix.tocoo()
    counts = np.rint(np.expm1(coo.data)).astype(int)
    actual = {
        (train.client_ids[i], train.product_ids[j]): n
        for i, j, n in zip(coo.row, coo.col, counts, strict=True)
    }
    assert actual == expected_counts.to_dict()

    test_only = set(
        zip(
            trades.loc[trades["trade_date"] >= cutoff, "client_id"],
            trades.loc[trades["trade_date"] >= cutoff, "product_id"],
            strict=True,
        )
    ) - set(expected_counts.index)
    assert test_only, "scenario should contain pairs that only occur in test"
    assert test_only.isdisjoint(actual)


# --- models ------------------------------------------------------------------


def fitted_models(data, train):
    segments = dict(
        zip(data["clients"]["client_id"], data["clients"]["segment"], strict=True)
    )
    return [
        SegmentPopularity().fit(train, segments),
        ItemItemCF().fit(train),
        SVDRecommender().fit(train, np.random.default_rng(0)),
    ]


@pytest.mark.parametrize("k", [3, 5])
def test_every_model_returns_k_unique_products(generated, k):
    data, train = generated
    for model in fitted_models(data, train):
        recs = model.recommend(train.client_ids, k, exclude_known=False)

        assert list(recs.columns) == RECO_COLUMNS
        per_client = recs.groupby("client_id")["product_id"]
        assert (per_client.nunique() == k).all(), model.name
        assert len(per_client) == len(train.client_ids)
        for ranks in recs.groupby("client_id")["rank"].agg(list):
            assert ranks == list(range(1, k + 1))


def test_exclude_known_never_returns_traded_products(generated):
    data, train = generated
    known = train.known_mask()
    for model in fitted_models(data, train):
        recs = model.recommend(train.client_ids, 5, exclude_known=True)
        rows = recs["client_id"].map(train.client_index)
        cols = recs["product_id"].map(train.product_index)
        assert not known[rows, cols].any(), model.name
        assert recs.groupby("client_id")["product_id"].nunique().le(5).all()


def test_segment_popularity_by_hand():
    trades = pd.DataFrame(
        [
            ("C1", "P1", "2026-01-01"),
            ("C2", "P1", "2026-01-01"),
            ("C2", "P2", "2026-01-01"),
            ("C2", "P2", "2026-01-02"),  # repeat trade does not add popularity
            ("C3", "P2", "2026-01-01"),  # other segment
        ],
        columns=["client_id", "product_id", "trade_date"],
    )
    train = build_interactions(trades, CLIENTS, PRODUCTS, AS_OF)
    model = SegmentPopularity().fit(train, {"C1": "a", "C2": "a", "C3": "b"})
    recs = model.recommend(["C1", "C3"], 2, exclude_known=False)

    assert list(recs.itertuples(index=False, name=None)) == [
        ("C1", "P1", 2.0, 1),
        ("C1", "P2", 1.0, 2),
        ("C3", "P2", 1.0, 1),
        ("C3", "P1", 0.0, 2),
    ]


def test_item_cf_scores_by_hand():
    trades = pd.DataFrame(
        [
            ("C1", "P1", "2026-01-01"),
            ("C2", "P1", "2026-01-01"),
            ("C2", "P2", "2026-01-01"),
        ],
        columns=["client_id", "product_id", "trade_date"],
    )
    train = build_interactions(trades, ["C1", "C2"], PRODUCTS, AS_OF)
    recs = ItemItemCF().fit(train).recommend(["C1"], 1, exclude_known=True)

    # Columns P1 = (1, 1), P2 = (0, 1) (log1p(1) each): cos = 1 / sqrt(2)
    assert recs["product_id"].tolist() == ["P2"]
    assert recs["score"].tolist() == pytest.approx([1 / math.sqrt(2)])


def test_unknown_client_raises(generated):
    data, train = generated
    model = ItemItemCF().fit(train)
    with pytest.raises(ValueError, match="Unknown"):
        model.recommend(["C9999"], 3)


# --- end to end --------------------------------------------------------------


def test_run_writes_report_and_logs_one_row_per_model_truth_and_k(tmp_path):
    con = get_connection(":memory:")
    load_all(con, generate_all(seed=42, as_of=AS_OF_DEFAULT), AS_OF_DEFAULT)
    report = tmp_path / "docs" / "evaluation.md"
    run(con, datetime(2026, 11, 20, 6, 0), report)

    text = report.read_text(encoding="utf-8")
    assert "| item_item_cf |" in text
    assert "precision@3" in text
    rows = con.execute(
        "SELECT model, ground_truth, k FROM vic.reco_eval_runs ORDER BY ALL"
    ).fetchall()
    assert len(rows) == 3 * 2 * 2
    assert len(set(rows)) == len(rows)
