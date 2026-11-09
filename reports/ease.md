# ease

Optimized on 2026-11-09T15:50 with 59 Optuna (TPE) trials, maximizing NDCG@10 on validation.

## Result

| Metric | Value |
|---|---|
| NDCG@10 on test | **0.0859** |
| NDCG@10 on validation (the one Optuna maximized) | 0.0877 |
| NDCG@10 of popularity on test (reference) | 0.0440 |
| Universe users the model knows (test) | 100.0% |
| Albums in the model (test) | 3543 |
| Time per trial | 1 s |
| Final model: reviews / users / albums | 40687 / 2301 / 3874 |

## Chosen hyperparameters

| Hyperparameter | Value |
|---|---|
| min_album | 1 |
| lambda | 46.33 |
| weight_artist | 9.474 |
| weight_genre | 0.6198 |
| weight_language | 2.792 |
| weight_decade | 0.9545 |

## Importance of each hyperparameter (fANOVA)

| Hyperparameter | Importance |
|---|---|
| weight_artist | 0.58 |
| lambda | 0.17 |
| min_album | 0.16 |
| weight_decade | 0.04 |
| weight_genre | 0.03 |
| weight_language | 0.03 |

## Best trials

| # | NDCG@10 (validation) | min_album | lambda | weight_artist | weight_genre | weight_language | weight_decade |
|---|---|---|---|---|---|---|---|
| 28 | 0.0877 | 1 | 46.33 | 9.474 | 0.6198 | 2.792 | 0.9545 |
| 47 | 0.0877 | 1 | 100.6 | 9.082 | 0.3252 | 1.82 | 5.992 |
| 32 | 0.0876 | 1 | 44.07 | 8.008 | 1.381 | 2.021 | 4.421 |
| 48 | 0.0874 | 1 | 57.29 | 9.017 | 0.2169 | 0.6048 | 8.977 |
| 55 | 0.0874 | 1 | 96.66 | 9.749 | 0.2363 | 0.07035 | 5.936 |
| 19 | 0.0872 | 1 | 174.2 | 7.187 | 1.974 | 5.11 | 6.405 |
| 49 | 0.0870 | 1 | 75.23 | 8.336 | 0.5047 | 0.288 | 7.124 |
| 44 | 0.0870 | 1 | 40.13 | 7.344 | 0.8225 | 2.694 | 8.269 |
| 35 | 0.0868 | 1 | 140.4 | 7.427 | 0.2271 | 3.528 | 1.671 |
| 39 | 0.0868 | 1 | 131.4 | 7.076 | 0.1135 | 1.774 | 2.682 |
