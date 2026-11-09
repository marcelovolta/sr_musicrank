# Music recommender

Flask app that recommends albums to users, built on a scrape of RateYourMusic (RYM): albums,
artists, genres and user reviews. It includes SQL-based algorithms, trained models (Surprise,
EASE, neural networks), an ensemble and cascades that choose by how well each user is known.

The detailed results are in [`reports/`](reports/summary.md), and a summary of the algorithms and their performance in [`reports/algorithms.md`](reports/algorithms.md).

## Structure

| File | What it does |
|---|---|
| `app.py` | The web app (Flask): login, recommendations, ratings. |
| `recommender.py` | The SQL algorithms, access to the models, the cascades. |
| `queries.py` | Every SQL query. |
| `dataset.py` | Database copy, reading the reviews, universe, album filter and train / validation / test split. |
| `models.py` | The trained models: search space, training and NumPy-only prediction. |
| `optimization.py` | Evaluation (NDCG@10), Optuna, final training, learning curves and reports. |
| `ensemble.py` | The LightGBM ensemble (and the simple average) over the models' predictions. |
| `pipeline.py` | The whole process, to run again when the database changes. |
| `templates/` | App pages. |
| `models/` | The trained models (`.pkl`), not versioned. |
| `reports/` | Markdown reports. |

## Usage

```bash
python pipeline.py                      # everything: copy, optimization, models, ensemble and reports
python pipeline.py --models ease svd    # only those models ('ensemble' needs the others already trained)
python pipeline.py --trials 5           # quick test
python pipeline.py --no-copy            # uses the existing work copy
python pipeline.py --resume             # picks up interrupted Optuna studies
flask --app app run                     # the app
```

Dependencies (Python 3.13): Flask, NumPy, pandas, SciPy, scikit-surprise, Keras with JAX,
Optuna, LightGBM and matplotlib.

## Data

- Reviews with a rating: RYM's (0.5 to 5 stars) plus this app's own ratings (1 to 10, halved;
  when a user rated an album in both places, the app rating wins).
- **Chronological order per user**, by review date; same-day ties by insertion order.
- **The data is sparse:** 62 thousand reviews from 17 thousand users, 3.7 per user on average.
  Only 1,076 users have 10 or more reviews.
- **Database copy:** the app writes to `data/mergenres.db` (ratings, seen), so the pipeline
  copies the file without opening it with SQLite, and accepts the copy only if the file didn't
  change while copying, there was no half-written transaction and it passes the integrity
  check. Everything else works on `data/work/mergenres.db`.

## Evaluation

- **Universe:** the users with at least 10 reviews. It's fixed, not a hyperparameter: if it
  changed between trials, each trial would be evaluated with other users and the NDCGs
  wouldn't be comparable.
- **Chronological split per user**, within the universe:

  | Part | Reviews of each user |
  |---|---|
  | Test | the last 3 |
  | Validation | the 2 before them |
  | Train | the rest (at least 5) |

  No model sees the future of what it's evaluated on, and **every user the models are trained
  with is also evaluated**, and vice versa.
- **Three stages:**
  1. Optuna trains on train and chooses the hyperparameters maximizing NDCG@10 on validation.
  2. With the best ones, the model is retrained on train + validation and measured on test.
     **That's the reported NDCG**: the test took part in no choice.
  3. The final model is trained with **every user with at least 5 reviews** (all of their
     reviews): as much history as the users with the least train in the optimization, so it
     also recommends to users below the evaluation universe.
- **Metric: NDCG@10** (the app shows 9 albums). Relevant = any reviewed album, whatever the
  rating: the goal is to guess what the user will listen to next.
- **Album filter:** albums with fewer than `min_album` reviews are dropped, counted only on the
  training data of each stage (so future reviews aren't used). It's a **hyperparameter of each
  model** and doesn't drop users.
- With 3 test albums per user and about a thousand users, the NDCG is noisy: differences below
  ~0.005 between models aren't meaningful.

## SQL algorithms (`recommender.py`)

They aren't trained: they query the database directly.

| Algorithm | What it recommends |
|---|---|
| `random` | Random albums. |
| `top_n` | Highest community rating on RYM, with at least 5 reviews. |
| `most_rated` | The most reviewed albums. |
| `balanced` | 0.4 × genre affinity + 0.4 × artist affinity + 0.2 × corrected album rating. |
| `tunnel_vision` | Albums matching both a genre **and** an artist the user reviewed; if they aren't enough, popular ones. |

All of them leave out what the user already rated or saw.

## Trained models (`models.py`)

Each model is saved as a dict of NumPy arrays, and predictions are computed with NumPy:
neither Surprise nor Keras are needed to recommend.

### baseline (BaselineOnly from Surprise)
Estimated rating = mean + user bias + album bias. Only the album bias matters for the order,
so it recommends the same to everyone.

### svd (SVD from Surprise)
Matrix factorization of the rating: users and albums as latent vectors.

### ease (Steck, 2019)
Linear album-to-album model with a closed-form solution: `B = I − P / diag(P)` with
`P = (XᵀX + λI)⁻¹` and the diagonal of B at 0. X is binary (reviewed or not). An album's score
is the sum of the weights given to it by the albums the user reviewed.

**Attributes (optional):** each artist, genre, language and decade is a pseudo-user that
"reviewed" every album with that value. That adds a weight to every pair of albums sharing the
attribute, as if they were somewhat co-listened. Optuna chooses the four weights (a tiny
weight means not using it).

### two_towers
One embedding per user and per album; score = cosine similarity. Trained with a softmax over
the **in-batch negatives**: each (user, chosen album) pair uses the albums of the other rows as
negatives. Each album gets the log of its frequency subtracted (**logQ correction**, Yi et al.
2019), because otherwise the popular ones show up too often as negatives and the model drifts
to them. Chosen = rating ≥ `positive_rating`.

**Attributes (optional, Optuna decides):** the album vector is the sum of the embeddings of
its id, artist, genre, language and decade. RYM users have no age or gender, so there are no
user attributes.

### big_tower
One network looks at each (user, album) pair: embeddings of the user, the album and its
attributes (artist, primary genre, language, release year), concatenated and passed through 3
dense layers of 256 down to a sigmoid. It learns to tell chosen albums from random unreviewed
ones (`negatives` per positive), drawn again every epoch because with fixed negatives the
network memorizes them.

### Changes from the class versions
- **No more KNN:** predicting album by album is too slow to evaluate it with Optuna.
- **The towers no longer approximate the rating** (MSE): they learn what the user chooses.
  Predicting a 4.5 doesn't say whether the user will listen to the album.
- **Hyperparameters chosen by Optuna** instead of the notebook's fixed ones.
- **No lazy training:** the app loads the `.pkl` files the pipeline writes.

## Ensemble (`ensemble.py`)

- **Candidates:** the first 50 albums of each model (SVD, EASE, two towers and big tower).
- **Features:** the score and position each model gives each candidate, and content features:
  the share of the user's reviews with the candidate's artist, genre and language, its release
  year and its popularity.
- **Model:** LightGBM with a LambdaRank objective reorders each user's candidates.
- **No data leakage (stacking):**
  - Stage A (validation): the base models are trained on train and LightGBM learns to predict
    the validation. Optuna chooses its hyperparameters fitting on 80% of the users and measuring
    on the other 20%.
  - Stage B (test): the base models are trained on train + validation and the stage A
    LightGBM is evaluated on test, like the other models.
  - Final: LightGBM is retrained on stage B and applied on the final models.
- **`average`:** the same candidates ordered by the simple average of the scores
  (standardized per user), as a reference and as an algorithm of its own.

## Cascades

`cascade_<model>` for each model and for the ensemble:

1. Up to 2 reviews (cold start): `top_n`.
2. If the model doesn't know the user (fewer than 5 reviews when it was trained):
   `tunnel_vision`, which can recommend any album.
3. The rest: the model.

## Results

NDCG@10 on test, over the 1,076 users of the universe (details in
[`reports/summary.md`](reports/summary.md)):

| Model | NDCG@10 (test) | vs popularity | NDCG@10 (validation) |
|---|---|---|---|
| ensemble | **0.0889** | 2.02× | 0.1059 |
| ease | 0.0859 | 1.95× | 0.0877 |
| *simple average* | *0.0795* | *1.81×* | |
| two_towers | 0.0716 | 1.63× | 0.0804 |
| big_tower | 0.0664 | 1.51× | 0.0616 |
| baseline | 0.0375 | 0.85× | 0.0308 |
| svd | 0.0349 | 0.79× | 0.0309 |
| *popularity* | *0.0440* | | |

- **Predicting what gets listened to beats predicting the rating.** EASE and the towers learn
  "reviewed or not" and double popularity; baseline and SVD learn the rating and fall below it.
- **Attributes help EASE, mainly the artist.** With the same hyperparameters, EASE goes from
  0.0770 to 0.0859 on test with attributes (+12%), and the artist alone gets 0.0844. Optuna gave
  the artist the largest weight (9.5) and it was the most important hyperparameter (fANOVA
  0.58). In two towers they didn't help: Optuna chose not to use them.
- **The ensemble adds a little over EASE** (+0.003, within the noise). Besides the models'
  scores, LightGBM uses the release year and the language affinity.
- **Cascades:** of the 16,861 users with reviews, 12,619 have up to 2 (top_n), about 1,940
  have 3 or 4 (tunnel_vision) and 2,301 have 5 or more (the model).

## Known limitations

- The models are trained with a copy of the database: what's rated in the app afterwards isn't
  seen until `pipeline.py` runs again.
- Users with fewer than 5 reviews get no personalized recommendations from the models: in the
  cascades `tunnel_vision` serves them.
- `recommender.py` rebuilds the stats tables of `data/mergenres.db` every time it's imported.
- `insert_rating` ignores a new rating if the user had already rated that album.
