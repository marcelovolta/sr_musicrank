# Recommendation algorithms and their performance

This document summarizes the album recommender's algorithms, how each one works and how they
compare. The details of each model are in its report ([baseline](baseline.md), [svd](svd.md),
[ease](ease.md), [two_towers](two_towers.md), [big_tower](big_tower.md),
[ensemble](ensemble.md)) and the whole process is in the [README](../README.md).

## Overview

| Family | Algorithms | Idea |
|---|---|---|
| SQL rules | `random`, `top_n`, `most_rated`, `balanced`, `tunnel_vision` | Fixed queries over the database, no training. |
| Trained models | `baseline`, `svd`, `ease`, `two_towers`, `big_tower` | Learn from the reviews; hyperparameters tuned with Optuna. |
| Ensemble | `ensemble`, `average` | Combine the recommendations of four models (LightGBM or simple average). |
| Cascades | `cascade_<model>` (one per model and one for the ensemble) | Choose the algorithm by how well the user is known. |

## How they're compared

- **Evaluation universe:** the 1,076 users with at least 10 reviews (32,758 reviews). It's
  fixed: every model is trained and evaluated with the same users.
- **Chronological split per user:** the last 3 reviews are test, the 2 before them validation
  and the rest (at least 5) train.
- **Three stages:** Optuna chooses the hyperparameters training on train and measuring on
  validation; with the best ones the model is retrained on train + validation and measured on
  test (the reported number, which took part in no choice); the final model is trained with
  every user with at least 5 reviews (2,301).
- **Metric: NDCG@10.** Whether the albums the user actually reviewed afterwards show up among
  the 10 recommended and how high (1 = perfect, 0 = no hit). Any reviewed album counts as a hit.
- **Reference:** recommending the most reviewed albums to everyone gives NDCG@10 = 0.0440.
- **Noise:** with 3 test albums per user and about a thousand users, differences below ~0.005
  aren't meaningful.

The SQL algorithms aren't evaluated with this protocol, because they query the whole database,
test included.

## Performance

### NDCG@10

| Rank | Model | NDCG@10 (test) | vs popularity | NDCG@10 (validation) |
|---|---|---|---|---|
| 1 | **ensemble** | **0.0889** | **2.02×** | 0.1059 |
| 2 | ease | 0.0859 | 1.95× | 0.0877 |
| — | *simple average* | *0.0795* | *1.81×* | |
| 3 | two_towers | 0.0716 | 1.63× | 0.0804 |
| 4 | big_tower | 0.0664 | 1.51× | 0.0616 |
| — | *popularity (reference)* | *0.0440* | *1.00×* | |
| 5 | baseline | 0.0375 | 0.85× | 0.0308 |
| 6 | svd | 0.0349 | 0.79× | 0.0309 |

```
ensemble    ██████████████████████████████████████████  0.0889
ease        █████████████████████████████████████████   0.0859
two_towers  ██████████████████████████████████          0.0716
big_tower   ███████████████████████████████             0.0664
popularity  █████████████████████                       0.0440
baseline    ██████████████████                          0.0375
svd         █████████████████                           0.0349
```

### What Optuna chose and what each model costs

| Model | min_album | Final model: users / albums | Time per trial | Key hyperparameters | Attributes |
|---|---|---|---|---|---|
| baseline | 6 | 2,298 / 1,628 | < 1 s | album reg. 156; 5 epochs | — |
| svd | 9 | 2,287 / 1,157 | < 1 s | 17 factors; 6 epochs | — |
| ease | 1 | 2,301 / 3,874 | 1 s | λ = 46 | yes: artist 9.5, language 2.8, decade 1.0, genre 0.6 |
| two_towers | 2 | 2,301 / 3,035 | 3 s | dimension 32; temperature 0.23; 29 epochs; positive = rating ≥ 1 | no |
| big_tower | 6 | 2,298 / 1,628 | 11 s | 7 negatives; 9 epochs; positive = rating ≥ 2.5 | always (artist, genre, language, year) |
| ensemble | — | — | — | 11 leaves; 438 trees | content features |

### What the attributes add

| Where | Without attributes | With attributes |
|---|---|---|
| EASE (test, same hyperparameters) | 0.0770 | **0.0859** |
| EASE, artist only (test) | 0.0770 | 0.0844 |
| Two towers (best trial on validation) | 0.0804 | 0.0782 |

### What the ensemble uses

LightGBM feature importance (gain, adding up each model's score and position):

| Features | Importance |
|---|---|
| two_towers | 22% |
| ease | 20% |
| big_tower | 16% |
| svd | 11% |
| **content** (release year 11%, language affinity 7%, genre affinity 6%, popularity 5%, artist affinity 4%) | **33%** |

### Cascades (16,861 users with reviews)

| Cascade | top_n (≤ 2 reviews) | tunnel_vision (3 or 4) | model (≥ 5) |
|---|---|---|---|
| cascade_ease, cascade_two_towers, cascade_ensemble | 12,619 | 1,941 | 2,301 |
| cascade_baseline, cascade_big_tower | 12,619 | 1,944 | 2,298 |
| cascade_svd | 12,619 | 1,955 | 2,287 |

(The models with a higher `min_album` lose a few users whose albums were all filtered out.)

## Interpretation

1. **Predicting what gets listened to beats predicting the rating.** EASE and the towers learn
   "reviewed or not" and double popularity. Baseline and SVD learn the rating and fall below
   popularity: knowing a user would give an album 4.5 stars doesn't say whether they'll listen
   to it.
2. **The simplest model is the best single model.** EASE, with a closed-form solution, beats
   both neural networks.
3. **In music the artist matters.** The attributes improve EASE by 12% on test, almost all of it
   from the artist: users review several albums by the same artist. (In the books project the
   attributes didn't help the single models.)
4. **The ensemble adds little** over EASE (+0.003, within the noise), even though a third of
   what LightGBM uses are content features. The simple average does worse than EASE alone.
5. **Most users can't be personalized:** 75% of the users with reviews have 1 or 2. The cascades
   give them `top_n`.

## Conclusion

- **Best option: `cascade_ensemble`** (0.0889), or **`cascade_ease`** (0.0859), which is
  practically as good and much simpler: one closed-form model of 3,874 × 3,874 albums.
- **Baseline and SVD** do worse than recommending the most reviewed albums.
