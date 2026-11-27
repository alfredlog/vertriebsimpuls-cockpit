"""Implicit client x product interaction matrix shared by all recommenders."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy import sparse

RECO_COLUMNS = ["client_id", "product_id", "score", "rank"]


@dataclass(frozen=True)
class InteractionMatrix:
    """Sparse client x product matrix plus the id <-> index mappings."""

    matrix: sparse.csr_matrix
    client_ids: list[str]
    product_ids: list[str]

    @property
    def client_index(self) -> dict[str, int]:
        """Map client_id -> row index."""
        return {client_id: i for i, client_id in enumerate(self.client_ids)}

    @property
    def product_index(self) -> dict[str, int]:
        """Map product_id -> column index."""
        return {product_id: j for j, product_id in enumerate(self.product_ids)}

    def known_mask(self) -> np.ndarray:
        """Dense boolean matrix: True where the client traded the product."""
        return self.matrix.toarray() > 0


def build_interactions(
    trades: pd.DataFrame,
    client_ids: list[str],
    product_ids: list[str],
    cutoff: date,
) -> InteractionMatrix:
    """Build log1p(number of trades) per client x product from trades before cutoff.

    All given clients and products get a row / column, also without trades, so
    indices stay stable between train and test and cold clients can be scored.
    cutoff is exclusive: a trade on the cutoff date belongs to the test window.
    """
    before_cutoff = trades[pd.to_datetime(trades["trade_date"]) < pd.Timestamp(cutoff)]
    counts = before_cutoff.groupby(["client_id", "product_id"]).size()

    client_index = {client_id: i for i, client_id in enumerate(client_ids)}
    product_index = {product_id: j for j, product_id in enumerate(product_ids)}
    rows = [client_index[c] for c in counts.index.get_level_values("client_id")]
    cols = [product_index[p] for p in counts.index.get_level_values("product_id")]
    values = np.log1p(counts.to_numpy(dtype=float))

    matrix = sparse.csr_matrix(
        (values, (rows, cols)), shape=(len(client_ids), len(product_ids))
    )
    return InteractionMatrix(matrix, list(client_ids), list(product_ids))


def top_k_frame(
    scores: np.ndarray,
    interactions: InteractionMatrix,
    client_ids: list[str],
    k: int,
    exclude_known: bool,
) -> pd.DataFrame:
    """Turn a dense client x product score matrix into the top-k recommendations.

    Ties are broken by catalog order so results are deterministic. With
    exclude_known a client may get fewer than k products if few are left.
    """
    client_index = interactions.client_index
    unknown = [c for c in client_ids if c not in client_index]
    if unknown:
        raise ValueError(f"Unknown client ids: {unknown}")

    rows = [client_index[c] for c in client_ids]
    client_scores = scores[rows].astype(float)
    if exclude_known:
        client_scores[interactions.known_mask()[rows]] = -np.inf

    records = []
    for client_id, row_scores in zip(client_ids, client_scores, strict=True):
        order = np.argsort(-row_scores, kind="stable")
        candidates = [j for j in order if np.isfinite(row_scores[j])][:k]
        for rank, j in enumerate(candidates, start=1):
            records.append(
                (client_id, interactions.product_ids[j], float(row_scores[j]), rank)
            )
    return pd.DataFrame(records, columns=RECO_COLUMNS)
