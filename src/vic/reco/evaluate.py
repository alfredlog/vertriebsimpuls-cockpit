"""Offline evaluation of the recommenders with a temporal split.

Train: trades before T = as_of - 90 days. Test: trades in [T, as_of].
Two ground truths per client:
- "new": products traded in the test window and never in train (discovery),
- "all": every product traded in the test window (incl. repeat business).
For "new" the models exclude already traded products, for "all" they do not.
"""

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from vic.db import DEFAULT_DB_PATH, get_connection, insert_frame, query_df
from vic.reco.baseline import SegmentPopularity
from vic.reco.interactions import InteractionMatrix, build_interactions
from vic.reco.item_cf import ItemItemCF
from vic.reco.svd import SVDRecommender

TEST_WINDOW_DAYS = 90
KS = (3, 5)
EVAL_SEED = 42
REPORT_PATH = Path("docs/evaluation.md")
GROUND_TRUTHS = ("new", "all")
METRICS = ("precision", "recall", "ndcg", "coverage")


# --- Metrics -----------------------------------------------------------------


def precision_at_k(recommended: list[str], relevant: set[str], k: int) -> float:
    """Share of the k slots filled with a relevant product."""
    hits = sum(1 for product in recommended[:k] if product in relevant)
    return hits / k


def recall_at_k(recommended: list[str], relevant: set[str], k: int) -> float:
    """Share of the relevant products found in the top k."""
    hits = sum(1 for product in recommended[:k] if product in relevant)
    return hits / len(relevant)


def ndcg_at_k(recommended: list[str], relevant: set[str], k: int) -> float:
    """Binary-relevance NDCG: hits near the top count more (1 / log2(rank + 1))."""
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, product in enumerate(recommended[:k], start=1)
        if product in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg


def catalog_coverage(recommendations: list[list[str]], n_products: int) -> float:
    """Share of the catalog recommended to at least one client."""
    recommended = {product for products in recommendations for product in products}
    return len(recommended) / n_products


# --- Split -------------------------------------------------------------------


def split_date(as_of: date) -> date:
    """Return T, the first day of the test window."""
    return as_of - timedelta(days=TEST_WINDOW_DAYS)


def ground_truth(
    trades: pd.DataFrame, as_of: date
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ("new", "all") relevant products per client for the test window."""
    trade_dates = pd.to_datetime(trades["trade_date"])
    cutoff = pd.Timestamp(split_date(as_of))
    train = trades[trade_dates < cutoff]
    test = trades[(trade_dates >= cutoff) & (trade_dates <= pd.Timestamp(as_of))]

    train_products = train.groupby("client_id")["product_id"].agg(set).to_dict()
    all_products = test.groupby("client_id")["product_id"].agg(set).to_dict()
    new_products = {
        client_id: products - train_products.get(client_id, set())
        for client_id, products in all_products.items()
    }
    new_products = {c: p for c, p in new_products.items() if p}
    return new_products, all_products


# --- Evaluation --------------------------------------------------------------


def score_recommendations(
    recommendations: pd.DataFrame, truth: dict[str, set[str]], k: int, n_products: int
) -> dict[str, float]:
    """Average precision, recall and NDCG over the clients in truth, plus coverage."""
    top_k = recommendations[recommendations["rank"] <= k]
    by_client = top_k.groupby("client_id")["product_id"].agg(list).to_dict()
    lists = [by_client.get(client_id, []) for client_id in truth]
    pairs = list(zip(lists, truth.values(), strict=True))
    return {
        "precision": float(np.mean([precision_at_k(r, t, k) for r, t in pairs])),
        "recall": float(np.mean([recall_at_k(r, t, k) for r, t in pairs])),
        "ndcg": float(np.mean([ndcg_at_k(r, t, k) for r, t in pairs])),
        "coverage": catalog_coverage(lists, n_products),
    }


def fit_models(
    train: InteractionMatrix, client_segments: dict[str, str], rng: np.random.Generator
) -> list:
    """Fit every model on the training matrix."""
    return [
        SegmentPopularity().fit(train, client_segments),
        ItemItemCF().fit(train),
        SVDRecommender().fit(train, rng),
    ]


def evaluate(
    trades: pd.DataFrame,
    clients: pd.DataFrame,
    product_ids: list[str],
    as_of: date,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Fit on train, score on test; return (results table, client counts)."""
    client_ids = clients["client_id"].tolist()
    client_segments = dict(zip(clients["client_id"], clients["segment"], strict=True))
    train = build_interactions(trades, client_ids, product_ids, split_date(as_of))
    new_truth, all_truth = ground_truth(trades, as_of)
    truths = {"new": new_truth, "all": all_truth}

    counts = {
        "n_clients": len(client_ids),
        "n_eval_all": len(all_truth),
        "n_excluded_all": len(client_ids) - len(all_truth),
        "n_eval_new": len(new_truth),
        "n_excluded_new": len(client_ids) - len(new_truth),
    }

    rows = []
    for model in fit_models(train, client_segments, rng):
        for truth_name in GROUND_TRUTHS:
            truth = truths[truth_name]
            recommendations = model.recommend(
                list(truth), max(KS), exclude_known=truth_name == "new"
            )
            for k in KS:
                metrics = score_recommendations(
                    recommendations, truth, k, len(product_ids)
                )
                rows.append(
                    {
                        "model": model.name,
                        "ground_truth": truth_name,
                        "k": k,
                        "n_clients": len(truth),
                        **metrics,
                    }
                )
    return pd.DataFrame(rows), counts


# --- Output ------------------------------------------------------------------


def render_markdown(results: pd.DataFrame, counts: dict[str, int], as_of: date) -> str:
    """Render one markdown table (model x metric) per ground truth."""
    lines = [
        "# Recommender evaluation",
        "",
        f"- as_of: {as_of.isoformat()}, split T: {split_date(as_of).isoformat()}"
        f" (test window {TEST_WINDOW_DAYS} days, inclusive)",
        f"- clients: {counts['n_clients']}; seed: {EVAL_SEED}",
        "- generated by `uv run python -m vic.reco.evaluate`",
        "",
    ]
    titles = {
        "new": (
            "New products (not traded in train; known products excluded)",
            counts["n_eval_new"],
            counts["n_excluded_new"],
        ),
        "all": (
            "All products traded in the test window (known products allowed)",
            counts["n_eval_all"],
            counts["n_excluded_all"],
        ),
    }
    header_metrics = [f"{m}@{k}" for k in KS for m in METRICS]
    for truth_name in GROUND_TRUTHS:
        title, n_eval, n_excluded = titles[truth_name]
        lines += [
            f"## {title}",
            "",
            f"Evaluated clients: {n_eval}; excluded (no relevant test trade):"
            f" {n_excluded}",
            "",
            "| model | " + " | ".join(header_metrics) + " |",
            "|---|" + "---:|" * len(header_metrics),
        ]
        subset = results[results["ground_truth"] == truth_name]
        for model, group in subset.groupby("model", sort=False):
            by_k = group.set_index("k")
            values = [f"{by_k.loc[k, m]:.3f}" for k in KS for m in METRICS]
            lines.append(f"| {model} | " + " | ".join(values) + " |")
        lines.append("")
    return "\n".join(lines)


def save_runs(
    con: duckdb.DuckDBPyConnection,
    results: pd.DataFrame,
    run_ts: datetime,
    as_of: date,
) -> None:
    """Append one row per model, ground truth and k to vic.reco_eval_runs."""
    runs = results.rename(
        columns={
            "precision": "precision_at_k",
            "recall": "recall_at_k",
            "ndcg": "ndcg_at_k",
        }
    )
    runs.insert(0, "run_ts", run_ts)
    runs.insert(1, "as_of", as_of)
    insert_frame(con, "reco_eval_runs", runs)


def run(
    con: duckdb.DuckDBPyConnection, run_ts: datetime, report_path: Path = REPORT_PATH
) -> pd.DataFrame:
    """Evaluate on the database's data, write the report and log the run."""
    as_of = con.execute("SELECT as_of FROM vic.run_context").fetchone()[0]
    trades = query_df(
        con, "SELECT client_id, product_id, trade_date FROM vic.fact_trades"
    )
    clients = query_df(
        con,
        "SELECT client_id, CAST(segment AS VARCHAR) AS segment"
        " FROM vic.dim_client ORDER BY client_id",
    )
    product_ids = query_df(
        con, "SELECT product_id FROM vic.dim_product ORDER BY product_id"
    )["product_id"].tolist()

    rng = np.random.default_rng(EVAL_SEED)
    results, counts = evaluate(trades, clients, product_ids, as_of, rng)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(results, counts, as_of), encoding="utf-8")
    save_runs(con, results, run_ts, as_of)
    return results


if __name__ == "__main__":
    with get_connection(DEFAULT_DB_PATH) as connection:
        run(connection, datetime.now(UTC).replace(tzinfo=None))
    print(f"Evaluation written to {REPORT_PATH}")
