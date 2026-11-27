# ADR 0002: Recommender approach – three simple models, chosen by offline evaluation

- Status: accepted
- Date: 2026-11-27
- Code: `src/vic/reco/`, `docs/evaluation.md`

## Context

The rules explain *why now*; a recommender must suggest *which product*. Data:
implicit feedback only (trades, no ratings), 300 clients, 16 products, two years of
history. Sales reps need suggestions they can explain, and the result must be
reproducible offline and in tests.

## Decision

- One implicit matrix: client x product, value `log1p(number of trades)`, built only
  from trades before a cutoff date (no leakage).
- Three models behind one interface (`fit`, `recommend(client_ids, k)`):
  1. **Baseline:** product popularity in the client's segment, counted as number of
     clients (not trades), so one heavy client cannot dominate.
  2. **Item-item CF:** cosine similarity between product columns, implemented by
     hand with numpy/scipy; score = sum of similarities to products already traded.
  3. **TruncatedSVD** (scikit-learn): low-rank reconstruction of the matrix.
- Temporal evaluation: train before `as_of - 90 days`, test in the 90 days up to
  `as_of`; Precision, Recall, NDCG and coverage at K = 3 and 5, for "new products"
  and "all products". Every run is stored in `vic.reco_eval_runs`.
- The daily run uses the model with the best NDCG@5 on new products in the latest
  evaluation run; without a run, the baseline.

## Result (2026-11-20)

On new products the baseline is best (NDCG@5 0.583 vs. 0.574 item-CF and 0.502
SVD); the CF difference is within noise (bootstrap 95 % CI includes 0). On all
products SVD is slightly better than the baseline (NDCG@5 0.541 vs. 0.494, CI
excludes 0). The synthetic data draws client preferences around the segment
profile, so segment popularity is close to the true generating process.

## Consequences

- The baseline is in production today; the other models are kept, evaluated on
  every run and switched in automatically if they win.
- The evaluation is honest about its limits: 72 evaluated clients on "new
  products", 16 products (coverage is trivial).
- Recommender scores are relative per client; ranking normalises them (ADR 0003).

## Alternatives considered

- **Implicit ALS / LightFM:** stronger on large sparse data, extra dependencies,
  no gain expected on a 300 x 16 matrix.
- **Content-based on product attributes only:** ignores what similar clients do.
- **Gradient boosting on hand-made features:** needs a labelled target and more
  data; harder to explain than "similar clients trade X".
