import datetime
import os
import sqlite3
import time

import numpy as np
import optuna
import pandas as pd

import dataset
import models
import optimization
import queries


# Ensemble of the trained models: a LightGBM (LambdaRank) reorders the
# candidates proposed by SVD, EASE, two towers and big tower, using as features
# the score and position each model gives each candidate, and content features:
# what share of the user's reviews has the candidate's artist, genre or
# language, the release year and the popularity.
#
# So there's no data leakage (stacking), LightGBM never learns from predictions
# of models that saw the answers, and the test takes part in no choice (same
# stages as the single models, see optimization.py):
# - stage A (validation): the base models are trained on train and LightGBM
#   learns to predict the validation. Optuna chooses its hyperparameters
#   training on 80% of the users and measuring on the other 20%;
# - stage B (test): the base models are trained on train + validation and the
#   stage A LightGBM is evaluated on test, like the other models (NDCG@10
#   comparable with their table);
# - final: LightGBM is retrained on stage B (the most recent answers) and is
#   applied on the final models.
# As a reference a simple average is also measured: the mean of each model's
# scores, standardized per user. It's also available as its own algorithm.
BASE_MODELS = ('svd', 'ease', 'two_towers', 'big_tower')
CANDIDATES = 50  # per model; the candidates are the union
TRIALS = 30
EVALUATION_SHARE = 0.2  # stage A users set aside for Optuna to measure on
CONTENT = ['affinity_artist', 'affinity_genre', 'affinity_language', 'release_year', 'popularity']
COLUMNS = [f'{n}_{v}' for n in BASE_MODELS for v in ('score', 'position')] + CONTENT


def album_table(database):
    # artist, genre and language (codes, -1 = missing) and release year of
    # every album, for the content features
    with sqlite3.connect(database) as conn:
        albums = pd.read_sql(queries.TOWER_ALBUM_FEATURES, conn)
    albums = albums.drop_duplicates('album_id').set_index('album_id')
    table = pd.DataFrame({a: albums[a].astype('category').cat.codes.astype(np.int32)
                          for a in ('artist', 'genre', 'language')}, index=albums.index)
    year = pd.to_numeric(albums['release_year'], errors='coerce')
    table['release_year'] = year.where(year > 0).astype(np.float32)
    return table


def _content(albums, username, m_ease, table):
    # content features of a user's candidate `albums`. What the user reviewed
    # comes from the EASE model of the same stage (the one with the largest
    # catalog), so it never includes what's being predicted
    t = table.reindex(albums)
    u = m_ease['user_index'].get(username)
    columns = {}
    if u is None:
        for a in ('artist', 'genre', 'language'):
            columns[f'affinity_{a}'] = np.full(len(albums), np.nan, dtype=np.float32)
    else:
        reviewed = table.reindex(m_ease['album_ids'][m_ease['listened'][u]])
        for a in ('artist', 'genre', 'language'):
            share = reviewed[a][reviewed[a] >= 0].value_counts() / max(len(reviewed), 1)
            columns[f'affinity_{a}'] = t[a].map(share).fillna(0).where(t[a] >= 0).to_numpy(dtype=np.float32)
    columns['release_year'] = t['release_year'].to_numpy(dtype=np.float32)
    j = pd.Index(m_ease['album_ids']).get_indexer(albums)
    columns['popularity'] = np.where(j >= 0, np.log1p(m_ease['popularity'][j]), np.nan).astype(np.float32)
    return columns


def candidates(ms, users, table, batch=300):
    # Candidates of each user (the first CANDIDATES of each model, without what
    # they already reviewed) with the score and position in each model and the
    # content features. If a model doesn't know the user or the album, its
    # columns stay empty (LightGBM handles it)
    indices = {n: pd.Index(m['album_ids']) for n, m in ms.items()}
    parts = []
    for start in range(0, len(users), batch):
        group = users[start:start + batch]
        per_model = {}
        for n, m in ms.items():
            known = [user for user in group if user in m['user_index']]
            if not known:
                continue
            us = np.array([m['user_index'][user] for user in known])
            s = np.asarray(models.scores(m, us), dtype=np.float32)
            rows = np.repeat(np.arange(len(us)), [len(m['listened'][u]) for u in us])
            s[rows, np.concatenate([m['listened'][u] for u in us])] = np.nan
            order = np.argsort(np.where(np.isnan(s), np.inf, -s), axis=1, kind='stable')
            position = np.empty_like(order)
            np.put_along_axis(position, order, np.arange(s.shape[1])[None, :], axis=1)
            for k, user in enumerate(known):
                top = order[k, :CANDIDATES]
                top = top[~np.isnan(s[k, top])]  # with a small catalog reviewed ones could slip in
                per_model.setdefault(user, {})[n] = (s[k], position[k], top)
        for user, info in per_model.items():
            albums = pd.unique(np.concatenate([ms[n]['album_ids'][top] for n, (_, _, top) in info.items()]))
            row = {'username': user, 'album_id': albums}
            for n in BASE_MODELS:
                if n in info:
                    s, position, _ = info[n]
                    j = indices[n].get_indexer(albums)
                    row[f'{n}_score'] = np.where(j >= 0, s[j], np.nan)
                    row[f'{n}_position'] = np.where(j >= 0, position[j] + 1, np.nan).astype(np.float32)
                else:
                    row[f'{n}_score'] = row[f'{n}_position'] = np.full(len(albums), np.nan, dtype=np.float32)
            row.update(_content(albums, user, ms['ease'], table))
            parts.append(pd.DataFrame(row))
    return pd.concat(parts, ignore_index=True)


def _with_labels(df, relevant):
    # 1 if the candidate is among what the user reviewed afterwards
    df['relevant'] = [a in relevant.get(user, ()) for user, a in zip(df['username'], df['album_id'])]
    return df


def _lists(df, score):
    # top K of each user by `score`
    df = df.assign(_s=score).sort_values(['username', '_s'], ascending=[True, False], kind='stable')
    return df.groupby('username')['album_id'].apply(lambda s: list(s[:optimization.K])).to_dict()


def _with_positives(df):
    # only the users with some relevant candidate: without positives there's nothing to learn
    return df[df.groupby('username')['relevant'].transform('any')]


def simple_average(df):
    # mean of each model's scores, standardized per user
    z = []
    for n in BASE_MODELS:
        g = df.groupby('username')[f'{n}_score']
        z.append((df[f'{n}_score'] - g.transform('mean')) / g.transform('std').replace(0, 1))
    return pd.concat(z, axis=1).mean(axis=1, skipna=True).fillna(-np.inf)


def _suggest(trial):
    # LightGBM's search space
    return {
        'num_leaves': trial.suggest_int('num_leaves', 8, 128, log=True),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
        'n_estimators': trial.suggest_int('n_estimators', 50, 500),
        'min_child_samples': trial.suggest_int('min_child_samples', 10, 300, log=True),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
        'reg_lambda': trial.suggest_float('reg_lambda', 1e-3, 10, log=True),
    }


def _fit(df, params):
    # LightGBM's native API (the scikit-learn style one requires scikit-learn).
    # Each user is a group: LambdaRank learns to order within each one
    import lightgbm as lgb
    df = df.sort_values('username', kind='stable')
    groups = df.groupby('username', sort=False).size().to_numpy()
    data_lgb = lgb.Dataset(df[COLUMNS], df['relevant'].astype(int), group=groups)
    return lgb.train({'objective': 'lambdarank', 'seed': models.SEED, 'verbose': -1, **params}, data_lgb)


def _bases(df, database):
    # the base models with their already chosen hyperparameters, trained on df
    ms = {}
    for n in BASE_MODELS:
        p = models.model(n)['params']
        ms[n] = models.train(n, dataset.filter_albums(df, p['min_album']), p, database)
    return ms


def optimize_and_train(train, validation, rest, test, database, trials=TRIALS):
    # stages A and B (see above), Optuna over LightGBM, final model and report.
    # rest = train + validation
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    start = time.time()
    table = album_table(database)

    # stage A: candidates of the models trained on train, labeled with the
    # validation. The users are split between fitting and evaluation
    relevant_a = validation.groupby('username')['album_id'].apply(set).to_dict()
    cand_a = _with_labels(candidates(_bases(train, database), sorted(relevant_a), table), relevant_a)
    users_a = np.array(sorted(relevant_a))
    np.random.default_rng(models.SEED).shuffle(users_a)
    held_out = set(users_a[:int(len(users_a) * EVALUATION_SHARE)])
    fit_part = _with_positives(cand_a[~cand_a['username'].isin(held_out)])
    cand_eval = cand_a[cand_a['username'].isin(held_out)]
    ev_a = optimization.Evaluator(train, validation[validation['username'].isin(held_out)])
    print(f'  ensemble stage A: {len(fit_part)} candidates to fit, {len(held_out)} users to evaluate', flush=True)

    def objective(trial):
        # LightGBM with the trial's hyperparameters, measured on the held-out users
        ranker = _fit(fit_part, _suggest(trial))
        value = ev_a.ndcg({**ev_a.fallback, **_lists(cand_eval, ranker.predict(cand_eval[COLUMNS]))})
        print(f'  ensemble trial {trial.number}: ndcg (validation) {value:.4f}', flush=True)
        return value

    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=models.SEED))
    study.optimize(objective, n_trials=trials)
    params = study.best_params

    # stage B: the stage A LightGBM (with all its users) evaluated on test, on
    # base models trained on train + validation
    ranker_a = _fit(_with_positives(cand_a), params)
    ev = optimization.Evaluator(rest, test)
    relevant_b = test.groupby('username')['album_id'].apply(set).to_dict()
    bases_b = _bases(rest, database)
    cand_b = _with_labels(candidates(bases_b, ev.users, table), relevant_b)

    def ndcg(lists):
        # NDCG@K on test of every user in the universe (fallback for the uncovered ones)
        return ev.ndcg({**ev.fallback, **lists})

    ndcg_test = ndcg(_lists(cand_b, ranker_a.predict(cand_b[COLUMNS])))
    reference = {n: ev.evaluate(m)['ndcg'] for n, m in bases_b.items()}
    reference['simple average'] = ndcg(_lists(cand_b, simple_average(cand_b)))
    print(f'  ensemble test: {ndcg_test:.4f}; references: { {k: round(v, 4) for k, v in reference.items()} }', flush=True)

    # final: LightGBM on stage B (the final base models are already saved)
    final = _fit(_with_positives(cand_b), params)
    m = {
        'name': 'ensemble',
        'params': params,
        'booster': final.model_to_string(),
        'albums': table,
        'optimization': {
            'date': datetime.datetime.now().isoformat(timespec='minutes'),
            'ndcg': ndcg_test,
            'ndcg_validation': study.best_value,
            'ndcg_popularity': ev.ndcg(ev.fallback),
            'reference': reference,
            'trials': trials,
            'users_covered': cand_b['username'].nunique() / len(ev.users),
            'importance': dict(zip(COLUMNS, final.feature_importance('gain').tolist())),
            'minutes': round((time.time() - start) / 60),
        },
    }
    models.save(m)
    report(m)
    return m


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

_booster = {}


def _ranker():
    # the saved model's LightGBM, loaded only once
    import lightgbm as lgb
    m = models.model('ensemble')
    if id(m) not in _booster:
        _booster.clear()
        _booster[id(m)] = lgb.Booster(model_str=m['booster'])
    return _booster[id(m)]


def knows_user(username):
    # it knows the user if any base model does
    return any(models.knows_user(n, username) for n in BASE_MODELS)


def ranking(username, average=False):
    # candidates of the final models reordered by LightGBM (or, with
    # average=True, by the simple average of the standardized scores). If no
    # model knows the user, the most reviewed albums (from EASE, the one with
    # the largest catalog)
    ms = {n: models.model(n) for n in BASE_MODELS}
    if not knows_user(username):
        yield from models.ranking('ease', username)
        return
    df = candidates(ms, [username], models.model('ensemble')['albums'])
    score = simple_average(df).to_numpy() if average else _ranker().predict(df[COLUMNS])
    for j in np.argsort(-score, kind='stable'):
        yield df['album_id'].iat[j], score[j]
    # if the user already saw every candidate (in the app), EASE takes over
    proposed = set(df['album_id'])
    yield from ((a, s) for a, s in models.ranking('ease', username) if a not in proposed)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(m):
    # reports/ensemble.md: comparison, feature importance and hyperparameters
    o = m['optimization']
    importance = sorted(o['importance'].items(), key=lambda x: -x[1])
    total = sum(v for _, v in importance) or 1
    k = optimization.K
    lines = [
        '# Ensemble (LightGBM)', '',
        f"Optimized on {o['date']} with {o['trials']} Optuna (TPE) trials, maximizing NDCG@{k} "
        'on validation (held-out users of stage A).', '',
        '## How it works', '',
        f'- Candidates: the first {CANDIDATES} albums of each base model ({", ".join(BASE_MODELS)}), merged.',
        '- Features: the score and position of each candidate in each model, and content ones: what share of the '
        "user's reviews has the candidate's artist, genre or language, the release year and the popularity.",
        '- LightGBM with a LambdaRank objective reorders the candidates.',
        '- No data leakage: LightGBM learns to predict the validation with base models trained on train, '
        'and is evaluated on test with base models trained on train + validation.',
        '', f'## Result (NDCG@{k} on test)', '',
        f'| Model | NDCG@{k} |', '|---|---|',
        f"| **LightGBM ensemble** | **{o['ndcg']:.4f}** |",
        *[f'| {n} | {v:.4f} |' for n, v in sorted(o['reference'].items(), key=lambda x: -x[1])],
        f"| popularity | {o['ndcg_popularity']:.4f} |",
        '', f"NDCG@{k} on validation (the one Optuna maximized): {o['ndcg_validation']:.4f}.",
        '', f"Universe users known by some base model: {o['users_covered']:.0%}.",
        '', '## Feature importance (gain)', '',
        '| Feature | Importance |', '|---|---|',
        *[f'| {n} | {v / total:.0%} |' for n, v in importance],
        '', '## LightGBM hyperparameters', '',
        '| Hyperparameter | Value |', '|---|---|',
        *[f'| {n} | {optimization.fmt(v)} |' for n, v in m['params'].items()],
    ]
    os.makedirs(optimization.REPORTS_DIR, exist_ok=True)
    with open(os.path.join(optimization.REPORTS_DIR, 'ensemble.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
