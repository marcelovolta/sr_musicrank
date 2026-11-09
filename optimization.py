import datetime
import os
import time

import numpy as np
import optuna

import dataset
import models


# Hyperparameter tuning with Optuna, maximizing NDCG@10, on the split from
# dataset.partitions (universe of users with at least 10 reviews; per user, the
# last 3 are test, the 2 before them validation and the rest train).
#
# Design decisions:
# - Three stages: Optuna trains on train and measures on validation; with the
#   best hyperparameters the model is retrained on train + validation and
#   measured on test (the reported NDCG: the test took part in no choice); the
#   final model is trained with every user with at least
#   dataset.MIN_USER_FINAL reviews, so it also recommends to users with less
#   history than the evaluation universe.
# - Every user in the universe is trained and evaluated, always the same ones
#   in every trial, so the NDCGs are comparable.
# - The minimum number of reviews per album is a hyperparameter and is counted
#   only on the training data of each stage, so future reviews aren't used.
# - Relevant = any album the user reviewed (the rating doesn't matter): the
#   goal is to guess what the user will listen to next.
# - K = 10: the app shows 9 albums.
K = 10
TRIALS = {'baseline': 50, 'svd': 50, 'ease': 60, 'two_towers': 50, 'big_tower': 30}
STUDIES = 'sqlite:///' + os.path.join(dataset.ROOT, 'data', 'work', 'optuna.db')
REPORTS_DIR = os.path.join(dataset.ROOT, 'reports')
DISCOUNT = 1 / np.log2(np.arange(2, K + 2))


class Evaluator:
    # Measures NDCG@K of a model trained on train against what the user
    # reviewed afterwards (validation or test)
    def __init__(self, train, test):
        # the test albums of each user, and the fallback list (most reviewed albums)
        self.test = test.groupby('username')['album_id'].apply(set).to_dict()
        self.users = sorted(self.test)
        reviewed = train.groupby('username')['album_id'].apply(set).to_dict()
        # fallback for users the model doesn't know (one the album filter left
        # with nothing, and the popularity reference): the most reviewed albums
        # of train (unfiltered) the user hasn't reviewed yet
        popular = train['album_id'].value_counts().index.to_numpy()
        self.fallback = {}
        for user in self.users:
            seen = reviewed.get(user, set())
            self.fallback[user] = [a for a in popular[:K + len(seen)] if a not in seen][:K]

    def ndcg(self, lists):
        # binary NDCG@K averaged over every evaluated user
        values = []
        for user in self.users:
            rel = self.test[user]
            hits = np.array([a in rel for a in lists[user][:K]] + [False] * (K - len(lists[user][:K])))
            values.append((hits * DISCOUNT).sum() / DISCOUNT[:min(len(rel), K)].sum())
        return float(np.mean(values))

    def evaluate(self, m):
        # the model's top K for the users it knows (fallback for the rest) and
        # its NDCG, plus coverage data
        lists = dict(self.fallback)
        known = [user for user in self.users if user in m['user_index']]
        indices = np.array([m['user_index'][user] for user in known], dtype=np.int64)
        k = min(K, len(m['album_ids']) - 1)
        for start in range(0, len(indices), 500):
            us = indices[start:start + 500]
            s = np.asarray(models.scores(m, us), dtype=np.float32)
            # without the albums the user already reviewed in train
            rows = np.repeat(np.arange(len(us)), [len(m['listened'][u]) for u in us])
            s[rows, np.concatenate([m['listened'][u] for u in us])] = -np.inf
            top = np.argpartition(-s, k, axis=1)[:, :k]
            top = np.take_along_axis(top, np.argsort(-np.take_along_axis(s, top, axis=1), axis=1, kind='stable'), axis=1)
            for user, row in zip(known[start:start + 500], top):
                lists[user] = list(m['album_ids'][row])
        return {'ndcg': self.ndcg(lists), 'users_covered': len(known) / len(self.users),
                'albums': len(m['album_ids']), 'model_users': len(m['user_index'])}


def optimize(name, train, validation, database, trials=None, resume=False):
    # Searches the hyperparameters with Optuna (TPE): trains on train and
    # measures on validation. The study is kept in data/work/optuna.db, so with
    # resume=True an interrupted one is picked up again
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    if not resume:
        try:
            optuna.delete_study(study_name=name, storage=STUDIES)
        except KeyError:
            pass
    study = optuna.create_study(study_name=name, storage=STUDIES, direction='maximize', load_if_exists=True,
                                sampler=optuna.samplers.TPESampler(seed=models.SEED))
    missing = (trials or TRIALS[name]) - len(study.trials)  # before enqueuing: an enqueued trial already counts
    if not study.trials:
        for params in models.INITIAL.get(name, []):
            study.enqueue_trial(params)
    ev = Evaluator(train, validation)

    def objective(trial):
        # trains with the trial's hyperparameters and returns its NDCG@K on validation
        start = time.time()
        params = models.suggest(name, trial)
        filtered = dataset.filter_albums(train, params['min_album'])
        r = ev.evaluate(models.train(name, filtered, params, database))
        r['seconds'] = round(time.time() - start)
        for k, v in r.items():
            trial.set_user_attr(k, v)
        print(f"  {name} trial {trial.number}: ndcg {r['ndcg']:.4f} ({r['seconds']} s) {params}", flush=True)
        return r['ndcg']

    if missing > 0:
        study.optimize(objective, n_trials=missing)
    return study


def best_params(name, study):
    # the hyperparameters of the best trial, complete (as suggest builds them)
    return models.suggest(name, optuna.trial.FixedTrial(study.best_params))


def measure_test(name, params, rest, test, database):
    # Retrains on train + validation (rest) and measures on test: this is the
    # reported NDCG. Also returns the popularity reference on test
    ev = Evaluator(rest, test)
    r = ev.evaluate(models.train(name, dataset.filter_albums(rest, params['min_album']), params, database))
    r['ndcg_popularity'] = ev.ndcg(ev.fallback)
    return r


def train_final(name, study, final_universe, database, result):
    # With the best hyperparameters: training on the final universe (all of its
    # reviews). The .pkl keeps the record of the optimization and of the test
    # measurement (result, from measure_test)
    params = best_params(name, study)
    filtered = dataset.filter_albums(final_universe, params['min_album'])
    m = models.train(name, filtered, params, database)
    m['optimization'] = {
        'date': datetime.datetime.now().isoformat(timespec='minutes'),
        'ndcg': result['ndcg'],
        'ndcg_validation': study.best_value,
        'ndcg_popularity': result['ndcg_popularity'],
        'evaluation': {**result, 'seconds': study.best_trial.user_attrs.get('seconds')},
        'trials': len(study.trials),
        'reviews': len(filtered),
    }
    models.save(m)
    return m


def curve(name, params, train, validation, database, epochs):
    # Learning curve of a network: with the chosen hyperparameters it's trained
    # on train for `epochs` (more than the chosen ones, to see what happens
    # afterwards) and each epoch the training loss and the NDCG@K on validation
    # are recorded (the number of epochs is a choice, so it's looked at on
    # validation and not on test). It shows whether the network keeps learning
    # (it lacks capacity or epochs) or starts to overfit.
    ev = Evaluator(train, validation)
    filtered = dataset.filter_albums(train, params['min_album'])
    points = []

    def on_epoch_end(epoch, loss, build):
        # records the network's loss and NDCG@K at this epoch
        points.append((epoch, loss, ev.evaluate(build())['ndcg']))
        print(f'  curve {name} epoch {epoch}: loss {loss:.4f}, ndcg {points[-1][2]:.4f}', flush=True)

    models.train(name, filtered, dict(params, epochs=epochs), database, on_epoch_end)
    _plot_curve(name, points, params['epochs'])
    return points


def _plot_curve(name, points, chosen_epochs):
    # two panels sharing the x axis (loss and NDCG have different scales: never
    # on one chart with two y axes)
    import matplotlib
    import matplotlib.ticker
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    surface, series, ink, muted, grid = '#fcfcfb', '#2a78d6', '#2b2a27', '#6b6a64', '#e4e3de'
    epoch, loss, ndcg = zip(*points)
    fig, axes = plt.subplots(2, 1, figsize=(7, 5.2), sharex=True, facecolor=surface)
    for ax, values, title in ((axes[0], loss, 'Training loss'), (axes[1], ndcg, f'NDCG@{K} on validation')):
        ax.set_facecolor(surface)
        ax.plot(epoch, values, color=series, linewidth=2, marker='o', markersize=4)
        ax.axvline(chosen_epochs, color=muted, linewidth=1, linestyle='--')
        ax.set_title(title, loc='left', fontsize=10, color=ink)
        ax.grid(axis='y', color=grid, linewidth=0.8)
        ax.tick_params(colors=muted, labelsize=8)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        for side in ('left', 'bottom'):
            ax.spines[side].set_color(grid)
    best = max(range(len(ndcg)), key=lambda k: ndcg[k])
    axes[1].annotate(f'max {ndcg[best]:.4f} (epoch {epoch[best]})', (epoch[best], ndcg[best]),
                     textcoords='offset points', xytext=(0, 8), ha='center', va='bottom', fontsize=8, color=ink)
    axes[1].margins(y=0.15)  # room for the max label
    axes[1].xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    axes[0].annotate(f'chosen epochs: {chosen_epochs}', (chosen_epochs, max(loss)),
                     textcoords='offset points', xytext=(4, -4), fontsize=8, color=muted, va='top')
    axes[1].set_xlabel('Epoch', fontsize=9, color=muted)
    fig.suptitle(f'{name}: learning curve', x=0.02, ha='left', fontsize=12, color=ink)
    fig.tight_layout()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    fig.savefig(os.path.join(REPORTS_DIR, f'curve_{name}.png'), dpi=150, facecolor=surface)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Markdown reports
# ---------------------------------------------------------------------------

def fmt(v):
    # numbers with 4 significant digits for the tables
    if isinstance(v, float):
        return f'{v:.4g}'
    return str(v)


def report_model(name, study, m, curve_points=None):
    # reports/<model>.md: best hyperparameters, importance and best trials
    o = m['optimization']
    ev = o['evaluation']
    lines = [
        f'# {name}', '',
        f"Optimized on {o['date']} with {o['trials']} Optuna (TPE) trials, maximizing NDCG@{K} on validation.", '',
        '## Result', '',
        '| Metric | Value |', '|---|---|',
        f"| NDCG@{K} on test | **{o['ndcg']:.4f}** |",
        f"| NDCG@{K} on validation (the one Optuna maximized) | {o['ndcg_validation']:.4f} |",
        f"| NDCG@{K} of popularity on test (reference) | {o['ndcg_popularity']:.4f} |",
        f"| Universe users the model knows (test) | {ev['users_covered']:.1%} |",
        f"| Albums in the model (test) | {ev['albums']} |",
        f"| Time per trial | {ev['seconds']} s |",
        f"| Final model: reviews / users / albums | {o['reviews']} / {len(m['user_index'])} / {len(m['album_ids'])} |",
        '', '## Chosen hyperparameters', '',
        '| Hyperparameter | Value |', '|---|---|',
        *[f'| {k} | {fmt(v)} |' for k, v in m['params'].items()],
    ]
    try:
        importance = optuna.importance.get_param_importances(study)
        lines += ['', '## Importance of each hyperparameter (fANOVA)', '',
                  '| Hyperparameter | Importance |', '|---|---|',
                  *[f'| {k} | {v:.2f} |' for k, v in importance.items()]]
    except Exception:
        pass
    complete = sorted((t for t in study.trials if t.value is not None), key=lambda t: -t.value)
    keys = list(m['params'])
    lines += ['', '## Best trials', '',
              f'| # | NDCG@{K} (validation) | ' + ' | '.join(keys) + ' |',
              '|---|---|' + '---|' * len(keys),
              *[f'| {t.number} | {t.value:.4f} | ' + ' | '.join(fmt(t.params.get(k)) for k in keys) + ' |'
                for t in complete[:10]]]
    if curve_points:
        lines += ['', '## Learning curve', '',
                  f"With the chosen hyperparameters, trained on train for more epochs than the chosen ones "
                  f"({m['params']['epochs']}, dashed line).", '',
                  f'![Learning curve of {name}](curve_{name}.png)', '',
                  f'| Epoch | Loss | NDCG@{K} (validation) |', '|---|---|---|',
                  *[f'| {e} | {p:.4f} | {n:.4f} |' for e, p, n in curve_points]]
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(os.path.join(REPORTS_DIR, f'{name}.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
