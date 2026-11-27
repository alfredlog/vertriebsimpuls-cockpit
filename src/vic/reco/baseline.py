"""Baseline recommender: the most traded products in the client's segment."""

import numpy as np
import pandas as pd

from vic.reco.interactions import InteractionMatrix, top_k_frame


class SegmentPopularity:
    """Score = number of clients in the same segment that traded the product.

    Counting clients instead of trades keeps one very active client from
    dominating the segment's favourites.
    """

    name = "baseline_segment_popularity"

    def fit(
        self, interactions: InteractionMatrix, client_segments: dict[str, str]
    ) -> "SegmentPopularity":
        """Count, per segment, how many clients traded each product."""
        traded = interactions.known_mask()
        segments = np.array([client_segments[c] for c in interactions.client_ids])

        popularity = {}
        for segment in np.unique(segments):
            popularity[segment] = traded[segments == segment].sum(axis=0)

        self.interactions = interactions
        self.scores = np.vstack([popularity[s] for s in segments]).astype(float)
        return self

    def recommend(
        self, client_ids: list[str], k: int, exclude_known: bool = True
    ) -> pd.DataFrame:
        """Return the top-k segment favourites per client."""
        return top_k_frame(self.scores, self.interactions, client_ids, k, exclude_known)
