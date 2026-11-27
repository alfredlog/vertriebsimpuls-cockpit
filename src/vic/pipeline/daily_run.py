"""Daily run: data -> quality checks -> signals -> recommendations -> ranking.

The input data is checked before it is loaded, so a failed check leaves the
previous day's database untouched. Impulses are checked again before they are
written. CLI: uv run python -m vic.pipeline.daily_run --as-of 2026-11-20 [--evaluate]
"""

import argparse
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from vic.data.generate import AS_OF_DEFAULT, generate_all
from vic.db import (
    DEFAULT_DB_PATH,
    DEFAULT_SEED,
    ensure_app_schema,
    get_connection,
    insert_frame,
    load_all,
    query_df,
)
from vic.market.fetch_rates import fetch_rates
from vic.quality.checks import (
    check_dataset,
    check_input_data,
    impulses_schema,
    raise_on_failure,
    store_quality_results,
)
from vic.ranking.score import rank_impulses
from vic.reco.baseline import SegmentPopularity
from vic.reco.evaluate import run as run_evaluation
from vic.reco.interactions import build_interactions
from vic.reco.item_cf import ItemItemCF
from vic.reco.svd import SVDRecommender
from vic.signals.rules import run_all_rules, suitable_products

logger = logging.getLogger(__name__)

RECO_CANDIDATES = 10
DEFAULT_MODEL = SegmentPopularity.name
# The daily list excludes known products, so "new" is the matching evaluation.
MODEL_SELECTION_GROUND_TRUTH = "new"
MODEL_SELECTION_K = 5
# The run holds the file for a few seconds; the app's short-lived connections
# release it within milliseconds, so waiting this long is generous.
PIPELINE_LOCK_TIMEOUT_S = 30.0
EVALUATION_REPORT = "evaluation.md"

BEST_MODEL_SQL = """
SELECT model
FROM vic.reco_eval_runs
WHERE run_ts = (SELECT max(run_ts) FROM vic.reco_eval_runs)
    AND ground_truth = ? AND k = ?
ORDER BY ndcg_at_k DESC, model
LIMIT 1
"""


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Log the start, end and duration of a pipeline step."""
    logger.info("start: %s", name)
    started = time.perf_counter()
    try:
        yield
    except Exception:
        logger.error("failed: %s (%.2f s)", name, time.perf_counter() - started)
        raise
    logger.info("done: %s (%.2f s)", name, time.perf_counter() - started)


def select_best_model(con: duckdb.DuckDBPyConnection) -> str:
    """Return the best model of the latest evaluation run, or the baseline."""
    row = con.execute(
        BEST_MODEL_SQL, [MODEL_SELECTION_GROUND_TRUTH, MODEL_SELECTION_K]
    ).fetchone()
    if row is None:
        logger.info("no evaluation run found, using %s", DEFAULT_MODEL)
        return DEFAULT_MODEL
    return row[0]


def keep_suitable(
    recommendations: pd.DataFrame, suitable: pd.DataFrame, k: int
) -> pd.DataFrame:
    """Drop products the client may not be offered, re-rank and keep the top k.

    Re-ranking matters: ranking uses rank 1 as the product for reactivation.
    """
    kept = recommendations.merge(suitable, on=["client_id", "product_id"])
    kept = kept.sort_values(["client_id", "rank"])
    kept["rank"] = kept.groupby("client_id").cumcount() + 1
    return kept[kept["rank"] <= k].reset_index(drop=True)


def recommend_all(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    model_name: str,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Fit the chosen model on all trades up to as_of; return suitable new products."""
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

    # build_interactions excludes the cutoff day; trades on as_of must count.
    interactions = build_interactions(
        trades, clients["client_id"].tolist(), product_ids, as_of + timedelta(days=1)
    )
    if model_name == SegmentPopularity.name:
        segments = dict(zip(clients["client_id"], clients["segment"], strict=True))
        model = SegmentPopularity().fit(interactions, segments)
    elif model_name == ItemItemCF.name:
        model = ItemItemCF().fit(interactions)
    elif model_name == SVDRecommender.name:
        model = SVDRecommender().fit(interactions, rng)
    else:
        raise ValueError(f"Unknown model: {model_name}")
    # Score the whole catalog first, so filtering cannot leave a client short.
    candidates = model.recommend(
        interactions.client_ids, len(product_ids), exclude_known=True
    )
    return keep_suitable(candidates, suitable_products(con), RECO_CANDIDATES)


def write_impulses(
    con: duckdb.DuckDBPyConnection, impulses: pd.DataFrame, run_date: date
) -> None:
    """Replace the impulses of run_date; other days and feedback stay untouched."""
    con.begin()
    try:
        con.execute("DELETE FROM vic_app.daily_impulses WHERE run_date = ?", [run_date])
        insert_frame(con, "daily_impulses", impulses, schema="vic_app")
        con.commit()
    except Exception:
        con.rollback()
        raise


def run_daily(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    run_ts: datetime,
    seed: int = DEFAULT_SEED,
    market_rates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run the whole daily pipeline and return the impulses that were written.

    market_rates=None fetches the ECB curve (with fallback); tests pass rates in.
    """
    rng = np.random.default_rng(seed)
    ensure_app_schema(con)

    with _step("generate data"):
        data = generate_all(seed=seed, as_of=as_of)
        if market_rates is None:
            market_rates = fetch_rates(as_of)

    with _step("quality checks (input)"):
        summary = check_input_data(data, as_of)
        store_quality_results(con, summary, run_ts, as_of)
        raise_on_failure(summary)

    with _step("load database"):
        load_all(con, data, as_of, market_rates)

    with _step("signals"):
        signals = run_all_rules(con, as_of)
        logger.info("%d signals", len(signals))

    with _step("recommendations"):
        model_name = select_best_model(con)
        recommendations = recommend_all(con, as_of, model_name, rng)
        logger.info("model %s, %d candidates", model_name, len(recommendations))

    with _step("ranking"):
        clients = query_df(con, "SELECT client_id, sales_rep_id FROM vic.dim_client")
        products = query_df(con, "SELECT product_id, name FROM vic.dim_product")
        impulses = rank_impulses(signals, recommendations, clients, products, as_of)

    with _step("quality checks (impulses)"):
        summary = check_dataset("daily_impulses", impulses, impulses_schema())
        store_quality_results(con, summary, run_ts, as_of)
        raise_on_failure(summary)

    with _step("write impulses"):
        write_impulses(con, impulses, as_of)
        logger.info(
            "%d impulses for %d sales reps",
            len(impulses),
            impulses["sales_rep_id"].nunique(),
        )
    return impulses


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run the daily impulse pipeline.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=AS_OF_DEFAULT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="afterwards evaluate the recommenders (the next run uses the best one)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run_ts = datetime.now(UTC).replace(tzinfo=None)
    with (
        _step(f"daily run as_of={args.as_of}"),
        get_connection(args.db, lock_timeout_s=PIPELINE_LOCK_TIMEOUT_S) as con,
    ):
        run_daily(con, args.as_of, run_ts, seed=args.seed)
        if args.evaluate:
            with _step("recommender evaluation"):
                run_evaluation(con, run_ts, args.db.parent / EVALUATION_REPORT)


if __name__ == "__main__":
    main()
