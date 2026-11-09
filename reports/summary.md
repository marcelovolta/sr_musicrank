# Recommender optimization

Generated on 2026-11-09 16:02 with a copy of `data/mergenres.db`.

## Data

- Reviews with a rating (RYM plus this app's): 62475, from 16861 users and 4254 albums.
- Model universe: the 1076 users with at least 10 reviews (32758 reviews).
- Chronological split per user: the last 3 reviews are test, the 2 before them validation and the rest train.
- Train: 27378 reviews. Validation: 2152. Test: 3228.

## Method

- Each model is tuned with Optuna (TPE): it trains on train and maximizes NDCG@10 on validation.
- With the best hyperparameters it is retrained on train + validation and measured on test: this is the reported NDCG (the test took part in no choice).
- The final model is trained with every user with at least 5 reviews (all of their reviews), with the chosen hyperparameters.
- Every user in the universe is trained and evaluated, always the same ones.
- The minimum number of reviews per album is a hyperparameter, counted only on the training data.
- Relevant = any reviewed album, whatever the rating.

## Results

Reference: recommending the most reviewed albums to everyone gives NDCG@10 = 0.0440 on test.

| Model | NDCG@10 (test) | vs popularity | NDCG@10 (validation) | Universe users it knows | min_album | Users (final) | Albums (final) | Trials |
|---|---|---|---|---|---|---|---|---|
| [baseline](baseline.md) | **0.0375** | 0.85× | 0.0308 | 100% | 6 | 2298 | 1628 | 50 |
| [svd](svd.md) | **0.0349** | 0.79× | 0.0309 | 99% | 9 | 2287 | 1157 | 50 |
| [ease](ease.md) | **0.0859** | 1.95× | 0.0877 | 100% | 1 | 2301 | 3874 | 59 |
| [two_towers](two_towers.md) | **0.0716** | 1.63× | 0.0804 | 100% | 2 | 2301 | 3035 | 49 |
| [big_tower](big_tower.md) | **0.0664** | 1.51× | 0.0616 | 100% | 6 | 2298 | 1628 | 30 |
| [ensemble](ensemble.md) | **0.0889** | 2.02× | 0.1059 | 100% | — | — | — | 30 |

## Cascades

Up to 2 reviews: top_n. If the model doesn't know the user (fewer than 5 reviews): tunnel_vision. The rest: the model.

How the 16861 users with reviews are split:

| Cascade | top_n | tunnel_vision | model |
|---|---|---|---|
| cascade_baseline | 12619 | 1944 | 2298 |
| cascade_svd | 12619 | 1955 | 2287 |
| cascade_ease | 12619 | 1941 | 2301 |
| cascade_two_towers | 12619 | 1941 | 2301 |
| cascade_big_tower | 12619 | 1944 | 2298 |
| cascade_ensemble | 12619 | 1941 | 2301 |
