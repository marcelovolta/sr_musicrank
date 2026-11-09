# svd

Optimized on 2026-11-09T15:49 with 50 Optuna (TPE) trials, maximizing NDCG@10 on validation.

## Result

| Metric | Value |
|---|---|
| NDCG@10 on test | **0.0349** |
| NDCG@10 on validation (the one Optuna maximized) | 0.0309 |
| NDCG@10 of popularity on test (reference) | 0.0440 |
| Universe users the model knows (test) | 99.3% |
| Albums in the model (test) | 889 |
| Time per trial | 0 s |
| Final model: reviews / users / albums | 32179 / 2287 / 1157 |

## Chosen hyperparameters

| Hyperparameter | Value |
|---|---|
| min_album | 9 |
| n_factors | 17 |
| n_epochs | 6 |
| lr_all | 0.001618 |
| reg_all | 0.007235 |

## Importance of each hyperparameter (fANOVA)

| Hyperparameter | Importance |
|---|---|
| n_epochs | 0.41 |
| lr_all | 0.34 |
| n_factors | 0.09 |
| reg_all | 0.09 |
| min_album | 0.07 |

## Best trials

| # | NDCG@10 (validation) | min_album | n_factors | n_epochs | lr_all | reg_all |
|---|---|---|---|---|---|---|
| 41 | 0.0309 | 9 | 17 | 6 | 0.001618 | 0.007235 |
| 42 | 0.0304 | 11 | 11 | 7 | 0.001059 | 0.007677 |
| 40 | 0.0304 | 8 | 20 | 6 | 0.001223 | 0.005682 |
| 30 | 0.0303 | 16 | 11 | 10 | 0.001318 | 0.01135 |
| 34 | 0.0298 | 19 | 15 | 8 | 0.001036 | 0.0102 |
| 46 | 0.0298 | 9 | 10 | 5 | 0.001656 | 0.01114 |
| 19 | 0.0296 | 6 | 23 | 9 | 0.001268 | 0.00664 |
| 17 | 0.0296 | 8 | 18 | 8 | 0.001282 | 0.009834 |
| 39 | 0.0295 | 19 | 22 | 11 | 0.00105 | 0.005968 |
| 47 | 0.0294 | 9 | 11 | 10 | 0.001359 | 0.008068 |
