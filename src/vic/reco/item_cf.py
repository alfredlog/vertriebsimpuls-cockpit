"""Item-item collaborative filtering, implemented by hand with numpy / scipy."""

import numpy as np
import pandas as pd
from scipy import sparse

from vic.reco.interactions import InteractionMatrix, top_k_frame


def cosine_similarity(matrix: sparse.spmatrix) -> np.ndarray:
    """Cosine similarity between the columns (products) of a client x product matrix.

    Products nobody traded have a zero norm; their similarity is 0, not NaN.
    The diagonal is set to 0 so a product does not recommend itself.
    """
    matrix = sparse.csc_matrix(matrix, dtype=float)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=0))).ravel()
    inverse_norms = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    normalized = matrix @ sparse.diags(inverse_norms)
    similarity = (normalized.T @ normalized).toarray()
    np.fill_diagonal(similarity, 0.0)
    return similarity


class ItemItemCF:
    """score(client, item) = sum of similarities to the items the client traded."""

    name = "item_item_cf"

    def fit(self, interactions: InteractionMatrix) -> "ItemItemCF":
        """Compute the item-item similarity on the weighted (log1p) matrix."""
        self.interactions = interactions
        self.similarity = cosine_similarity(interactions.matrix)
        traded = interactions.known_mask().astype(float)
        self.scores = traded @ self.similarity
        return self

    def recommend(
        self, client_ids: list[str], k: int, exclude_known: bool = True
    ) -> pd.DataFrame:
        """Return the top-k items most similar to the client's history."""
        return top_k_frame(self.scores, self.interactions, client_ids, k, exclude_known)
