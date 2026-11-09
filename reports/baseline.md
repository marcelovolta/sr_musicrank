# baseline

Optimized on 2026-11-09T15:48 with 50 Optuna (TPE) trials, maximizing NDCG@10 on validation.

## Result

| Metric | Value |
|---|---|
| NDCG@10 on test | **0.0375** |
| NDCG@10 on validation (the one Optuna maximized) | 0.0308 |
| NDCG@10 of popularity on test (reference) | 0.0440 |
| Universe users the model knows (test) | 99.6% |
| Albums in the model (test) | 1330 |
| Time per trial | 0 s |
| Final model: reviews / users / albums | 35441 / 2298 / 1628 |

## Chosen hyperparameters

| Hyperparameter | Value |
|---|---|
| min_album | 6 |
| reg_i | 156.1 |
| reg_u | 30.4 |
| n_epochs | 5 |

## Importance of each hyperparameter (fANOVA)

| Hyperparameter | Importance |
|---|---|
| min_album | 0.51 |
| reg_i | 0.37 |
| reg_u | 0.09 |
| n_epochs | 0.03 |

## Best trials

| # | NDCG@10 (validation) | min_album | reg_i | reg_u | n_epochs |
|---|---|---|---|---|---|
| 35 | 0.0308 | 6 | 156.1 | 30.4 | 5 |
| 23 | 0.0308 | 9 | 90.36 | 123.9 | 14 |
| 22 | 0.0308 | 9 | 170.4 | 30.8 | 14 |
| 36 | 0.0308 | 9 | 179.3 | 28.5 | 4 |
| 19 | 0.0306 | 14 | 125.8 | 44.22 | 11 |
| 28 | 0.0306 | 10 | 111.8 | 77.44 | 19 |
| 18 | 0.0305 | 9 | 171 | 26.35 | 10 |
| 44 | 0.0304 | 18 | 195.1 | 15.78 | 10 |
| 42 | 0.0304 | 9 | 106.4 | 97.33 | 8 |
| 26 | 0.0304 | 20 | 152 | 104.6 | 20 |
