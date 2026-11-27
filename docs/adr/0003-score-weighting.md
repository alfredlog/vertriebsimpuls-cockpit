# ADR 0003: Weighting of rule signals and recommender scores

- Status: accepted
- Date: 2026-11-27
- Code: `src/vic/ranking/score.py`

## Context

Two engines feed the daily impulse list of each sales rep:

- **Rules** (`signals/`) explain *why now*: a maturity in the next 30 days, a large
  rate move, an inactive client, a concentrated portfolio. Each signal has a
  strength in 0..1 and a German reason with concrete facts.
- **Recommender** (`reco/`) explains *which product*: a score per client and product
  learned from trading history. Scores have no fixed scale and no time dimension.

The sales rep sees only the top 10 per day, so the combination decides what is
actually discussed with clients.

## Decision

```
score = W_RULE * signal_strength + W_RECO * normalized_reco_score
        + AGREEMENT_BONUS  (if a rule and the recommender name the same product)

W_RULE = 0.7, W_RECO = 0.3, AGREEMENT_BONUS = 0.1, TOP_N = 10 per sales rep
```

- `normalized_reco_score` = the client's recommender score divided by the client's
  best score (best candidate = 1). Scores of different models become comparable.
- One row per client and product; with several signals, the strongest one gives
  `signal_type` and `reason_de`.
- Signals without a product (reactivation) take the client's top recommended
  product. They get no agreement bonus, because the recommender chose the product.
- If only the recommender fired, `reason_de` is
  "Kunden mit ähnlichem Profil handeln <Produkt>".
- The recommender model is the one with the best NDCG@5 on "new products" in the
  latest evaluation run (`vic.reco_eval_runs`), because the daily list excludes
  products the client already trades. Without an evaluation run the segment
  popularity baseline is used.

## Why rules weigh more

1. **Explainable.** A rule signal comes with verifiable facts (date, amount, bp).
   A sales rep can open the call with it. "Similar clients trade X" is a weaker
   reason to call.
2. **Time-critical.** A maturity or a rate move has a deadline: the money is
   reinvested elsewhere or the hedge gets more expensive. A recommendation is
   just as valid next week.
3. **Measured quality.** In our offline evaluation (`docs/evaluation.md`) no model
   clearly beats the segment popularity baseline on new products, so the
   recommender should not dominate the list.

With these weights every rule signal beats every pure recommendation: the weakest
signal that fires has strength 0.5, so 0.7 * 0.5 = 0.35, while a recommendation
reaches at most 0.3 * 1.0 = 0.3. That is intended. Among rule signals the
recommender acts as a tie-breaker, and the bonus favours cases where both engines
agree.

## Consequences

- Pure recommendations only fill the list when a sales rep has fewer than 10 rule
  signals.
- Maturity signals propose a product the client already traded. The daily
  recommender excludes known products, so maturity signals never get the bonus.
  That is fine: their strength comes from the deadline.
- The weights are hand-set, not learned. Once enough feedback
  (`vic_app.impulse_feedback`) has been collected, they can be fitted on the
  acceptance rate.

## Alternatives considered

- **Rules as a hard filter, recommender only for product choice:** simpler, but
  clients without a signal would never appear.
- **Learned weights (logistic regression on accept/reject):** the right next step,
  but impossible without feedback data.
- **Rank fusion (e.g. reciprocal rank):** ignores how strong a signal is.
