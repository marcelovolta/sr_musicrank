import argparse
import datetime
import os
import warnings

import pandas as pd

import dataset
import ensemble
import models
import optimization


# The whole process, to run again whenever the database changes:
#   1. copies data/mergenres.db to data/work/mergenres.db (without locking the
#      original, which the app writes to)
#   2. universe (users with at least 10 reviews) and chronological split per
#      user: last 3 test, 2 before them validation, rest train
#   3. for each model: Optuna (train → validation), test measurement
#      (train + validation → test), final training with the users with at
#      least 5 reviews (all of their reviews), models/<model>.pkl and
#      reports/<model>.md (the networks also get their learning curve:
#      reports/curve_<model>.png)
#   4. ensemble: LightGBM over the predictions of SVD, EASE and the towers
#      (ensemble.py), with models/ensemble.pkl and reports/ensemble.md
#   5. reports/summary.md
#
#   python pipeline.py                         everything
#   python pipeline.py --models ease svd       only those models (and their reports);
#                                              'ensemble' needs the base models already trained
#   python pipeline.py --trials 5              fewer trials (quick test)
#   python pipeline.py --no-copy               uses the existing work copy
#   python pipeline.py --resume                picks up interrupted Optuna studies (or reuses
#                                              finished ones: only the final training is redone)


CURVE_EPOCHS = {'two_towers': 30, 'big_tower': 20}  # minimum epochs of the learning curves


def optimize_models(names, trials=None, resume=False):
    # steps 2 to 4: split, and per model Optuna, test measurement, final
    # training, learning curve and report
    df = dataset.read_reviews()
    universe, train, validation, test = dataset.partitions(df)
    rest = pd.concat([train, validation])
    final_universe = dataset.universe(df, dataset.MIN_USER_FINAL)
    print(f"{len(df)} reviews; universe: {len(universe)} from {universe['username'].nunique()} users "
          f'(train {len(train)}, validation {len(validation)}, test {len(test)})', flush=True)
    for name in names:
        print(f'== {name}', flush=True)
        if name == 'ensemble':
            m = ensemble.optimize_and_train(train, validation, rest, test, dataset.WORK_DB, trials or ensemble.TRIALS)
            print(f"   test ndcg {m['optimization']['ndcg']:.4f} with {m['params']}", flush=True)
            continue
        study = optimization.optimize(name, train, validation, dataset.WORK_DB, trials, resume)
        params = optimization.best_params(name, study)
        result = optimization.measure_test(name, params, rest, test, dataset.WORK_DB)
        m = optimization.train_final(name, study, final_universe, dataset.WORK_DB, result)
        points = None
        if name in CURVE_EPOCHS:
            # twice the chosen epochs, with a minimum, to see what happens afterwards
            epochs = max(CURVE_EPOCHS[name], 2 * m['params']['epochs'])
            points = optimization.curve(name, m['params'], train, validation, dataset.WORK_DB, epochs)
        optimization.report_model(name, study, m, points)
        print(f"   validation ndcg {study.best_value:.4f}, test {result['ndcg']:.4f} with {m['params']}", flush=True)


def summary_report():
    # reports/summary.md: comparison table of the models and how the cascades
    # split the users
    df = dataset.read_reviews()
    universe, train, validation, test = dataset.partitions(df)
    k = optimization.K
    count = df.groupby('username').size()
    cold = int((count <= 2).sum())

    rows, split_rows, ndcg_pop = [], [], float('nan')
    for name in models.NAMES:
        try:
            m = models.model(name)
        except FileNotFoundError:
            continue
        o, p = m['optimization'], m['params']
        ndcg_pop = o['ndcg_popularity']
        rows.append(f"| [{name}]({name}.md) | **{o['ndcg']:.4f}** | {o['ndcg'] / ndcg_pop:.2f}× "
                    f"| {o['ndcg_validation']:.4f} | {o['evaluation']['users_covered']:.0%} | {p['min_album']} "
                    f"| {len(m['user_index'])} | {len(m['album_ids'])} | {o['trials']} |")
        known = int(sum(c > 2 and u in m['user_index'] for u, c in count.items()))
        split_rows.append(f'| cascade_{name} | {cold} | {len(count) - cold - known} | {known} |')
    try:
        o = models.model('ensemble')['optimization']
        rows.append(f"| [ensemble](ensemble.md) | **{o['ndcg']:.4f}** | {o['ndcg'] / o['ndcg_popularity']:.2f}× "
                    f"| {o['ndcg_validation']:.4f} | {o['users_covered']:.0%} | — | — | — | {o['trials']} |")
        known = int(sum(c > 2 and ensemble.knows_user(u) for u, c in count.items()))
        split_rows.append(f'| cascade_ensemble | {cold} | {len(count) - cold - known} | {known} |')
    except FileNotFoundError:
        pass

    lines = [
        '# Recommender optimization', '',
        f"Generated on {datetime.datetime.now():%Y-%m-%d %H:%M} with a copy of `data/mergenres.db`.", '',
        '## Data', '',
        f"- Reviews with a rating (RYM plus this app's): {len(df)}, from {df['username'].nunique()} users "
        f"and {df['album_id'].nunique()} albums.",
        f"- Model universe: the {universe['username'].nunique()} users with at least {dataset.MIN_USER} reviews "
        f"({len(universe)} reviews).",
        f"- Chronological split per user: the last {dataset.N_TEST} reviews are test, the "
        f"{dataset.N_VALIDATION} before them validation and the rest train.",
        f"- Train: {len(train)} reviews. Validation: {len(validation)}. Test: {len(test)}.",
        '', '## Method', '',
        f'- Each model is tuned with Optuna (TPE): it trains on train and maximizes NDCG@{k} on validation.',
        '- With the best hyperparameters it is retrained on train + validation and measured on test: this is the '
        'reported NDCG (the test took part in no choice).',
        f'- The final model is trained with every user with at least {dataset.MIN_USER_FINAL} reviews '
        '(all of their reviews), with the chosen hyperparameters.',
        '- Every user in the universe is trained and evaluated, always the same ones.',
        '- The minimum number of reviews per album is a hyperparameter, counted only on the training data.',
        '- Relevant = any reviewed album, whatever the rating.',
        '', '## Results', '',
        f'Reference: recommending the most reviewed albums to everyone gives NDCG@{k} = {ndcg_pop:.4f} on test.', '',
        f'| Model | NDCG@{k} (test) | vs popularity | NDCG@{k} (validation) | Universe users it knows | min_album '
        '| Users (final) | Albums (final) | Trials |',
        '|---|---|---|---|---|---|---|---|---|',
        *rows,
        '', '## Cascades', '',
        f'Up to 2 reviews: top_n. If the model doesn\'t know the user (fewer than {dataset.MIN_USER_FINAL} '
        'reviews): tunnel_vision. The rest: the model.', '',
        f'How the {len(count)} users with reviews are split:', '',
        '| Cascade | top_n | tunnel_vision | model |', '|---|---|---|---|',
        *split_rows,
    ]
    os.makedirs(optimization.REPORTS_DIR, exist_ok=True)
    with open(os.path.join(optimization.REPORTS_DIR, 'summary.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Tunes and trains the models and writes the reports')
    names = [*models.NAMES, 'ensemble']  # the ensemble goes last: it uses the others
    parser.add_argument('--models', nargs='+', choices=names, default=names)
    parser.add_argument('--trials', type=int)
    parser.add_argument('--no-copy', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    warnings.filterwarnings('ignore')  # harmless JAX and Surprise warnings

    if not args.no_copy:
        print('copy:', dataset.copy_database(), flush=True)
    optimize_models(args.models, args.trials, args.resume)
    summary_report()
