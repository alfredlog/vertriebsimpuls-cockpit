"""Matrix factorisation recommender based on scikit-learn's TruncatedSVD."""

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD

from vic.reco.interactions import InteractionMatrix, top_k_frame

N_COMPONENTS = 6
# Upper bound for the integer seed scikit-learn expects as random_state.
MAX_SEED = 2**31 - 1


class SVDRecommender:
    """score = low-rank reconstruction of the interaction matrix."""

    name = "truncated_svd"

    def __init__(self, n_components: int = N_COMPONENTS) -> None:
        self.n_components = n_components

    def fit(
        self, interactions: InteractionMatrix, rng: np.random.Generator
    ) -> "SVDRecommender":
        """Factorise the matrix; the seed comes from the shared Generator."""
        model = TruncatedSVD(
            n_components=self.n_components,
            random_state=int(rng.integers(MAX_SEED)),
        )
        client_factors = model.fit_transform(interactions.matrix)
        self.interactions = interactions
        self.scores = client_factors @ model.components_
        return self

    def recommend(
        self, client_ids: list[str], k: int, exclude_known: bool = True
    ) -> pd.DataFrame:
        """Return the top-k products by reconstructed score."""
        return top_k_frame(self.scores, self.interactions, client_ids, k, exclude_known)
