# Ensemble (LightGBM)

Optimized on 2026-11-09T16:02 with 30 Optuna (TPE) trials, maximizing NDCG@10 on validation (held-out users of stage A).

## How it works

- Candidates: the first 50 albums of each base model (svd, ease, two_towers, big_tower), merged.
- Features: the score and position of each candidate in each model, and content ones: what share of the user's reviews has the candidate's artist, genre or language, the release year and the popularity.
- LightGBM with a LambdaRank objective reorders the candidates.
- No data leakage: LightGBM learns to predict the validation with base models trained on train, and is evaluated on test with base models trained on train + validation.

## Result (NDCG@10 on test)

| Model | NDCG@10 |
|---|---|
| **LightGBM ensemble** | **0.0889** |
| ease | 0.0859 |
| simple average | 0.0795 |
| two_towers | 0.0716 |
| big_tower | 0.0664 |
| svd | 0.0349 |
| popularity | 0.0440 |

NDCG@10 on validation (the one Optuna maximized): 0.1059.

Universe users known by some base model: 100%.

## Feature importance (gain)

| Feature | Importance |
|---|---|
| ease_score | 12% |
| two_towers_position | 11% |
| two_towers_score | 11% |
| release_year | 11% |
| big_tower_score | 9% |
| ease_position | 8% |
| affinity_language | 7% |
| big_tower_position | 7% |
| svd_position | 6% |
| affinity_genre | 6% |
| svd_score | 5% |
| popularity | 5% |
| affinity_artist | 4% |

## LightGBM hyperparameters

| Hyperparameter | Value |
|---|---|
| num_leaves | 11 |
| learning_rate | 0.02856 |
| n_estimators | 438 |
| min_child_samples | 83 |
| colsample_bytree | 0.7167 |
| reg_lambda | 0.522 |
