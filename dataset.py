import os
import shutil
import sqlite3
import time

import pandas as pd


# Data to train and evaluate the models: a copy of the database, the reviews in
# chronological order, the user universe, the album filter and the
# train / validation / test split.
ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCE_DB = os.path.join(ROOT, 'data', 'mergenres.db')
WORK_DB = os.path.join(ROOT, 'data', 'work', 'mergenres.db')
MIN_USER = 10         # evaluation universe: users with at least 10 reviews
N_TEST = 3            # the last 3 reviews of each user are test
N_VALIDATION = 2      # the 2 before them, validation (the rest, train: at least 5)
MIN_USER_FINAL = 5    # universe of the final model (the one that recommends): see universe


def copy_database(source=SOURCE_DB, target=WORK_DB, attempts=30):
    # The app writes to the source database (ratings, seen), so it's never opened
    # with SQLite here: the file is copied without locks, and the copy is accepted
    # only if the file didn't change while copying, there was no half-written
    # transaction (-journal file) and the copy passes the integrity check.
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for _ in range(attempts):
        before = os.stat(source)
        in_transaction = os.path.exists(source + '-journal')
        shutil.copyfile(source, target + '.tmp')
        after = os.stat(source)
        if not in_transaction and not os.path.exists(source + '-journal') \
                and (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            with sqlite3.connect(target + '.tmp') as conn:
                ok = conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            if ok:
                os.replace(target + '.tmp', target)
                return target
        time.sleep(2)
    raise RuntimeError(f'Could not get a consistent copy of {source}')


def read_reviews(database=WORK_DB):
    # Reviews with a rating: RYM's plus this app's own ratings, both in stars
    # (0.5 to 5 in steps of 0.5); when a user rated an album in both places,
    # the app rating wins. Sorted chronologically per user; same
    # day ties are broken by insertion order, and app ratings go after RYM's.
    with sqlite3.connect(database) as conn:
        rym = pd.read_sql('SELECT username, album_id, rating, review_date AS date, id AS ord '
                          'FROM user_reviews WHERE COALESCE(rating, 0) > 0', conn)
        app = pd.read_sql('SELECT username, album_id, rating, created_at AS date, rowid AS ord '
                          'FROM ratings WHERE COALESCE(rating, 0) > 0', conn)
    app['ord'] += rym['ord'].max() + 1 if len(rym) else 0
    rym = rym.merge(app[['username', 'album_id']], how='left', indicator=True)
    rym = rym[rym['_merge'] == 'left_only'].drop(columns='_merge')
    df = pd.concat([rym, app], ignore_index=True)
    df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce').fillna(pd.Timestamp.min)
    return df.sort_values(['username', 'date', 'ord'], kind='stable').reset_index(drop=True)


def universe(df, min_user=MIN_USER):
    # Users of the model universe: those with at least min_user reviews in
    # total. All of them have test, validation and at least
    # min_user - N_TEST - N_VALIDATION train reviews, so every user the models
    # are trained with is also evaluated, and vice versa. It's a fixed value (not
    # a hyperparameter): if it changed between trials, each trial would be
    # evaluated with other users and the NDCGs wouldn't be comparable.
    # The final model uses a wider universe (MIN_USER_FINAL: as many reviews as
    # the minimum train of the optimization) to also recommend to users with that
    # much history, with the hyperparameters chosen in the evaluation universe.
    per_user = df.groupby('username')['album_id'].transform('size')
    return df[per_user >= min_user]


def filter_albums(df, min_album):
    # Drops the albums with fewer than min_album reviews in df (an album with
    # almost no listeners can't be learned well). It's counted on the training
    # data of each stage, so future reviews aren't used. It doesn't drop users:
    # the universe is fixed.
    per_album = df.groupby('album_id')['username'].transform('size')
    return df[per_album >= min_album]


def split(df, n):
    # Chronological split per user: the last n reviews of each user on one side
    # and the earlier ones on the other. No model sees the future of what it's
    # evaluated on.
    position_from_end = df.groupby('username').cumcount(ascending=False)
    last = position_from_end < n
    return df[~last], df[last]


def partitions(df):
    # Universe and split into three parts per user:
    #   test: the last N_TEST; measures the reported result
    #   validation: the N_VALIDATION before them; Optuna chooses the
    #     hyperparameters with them, so the test takes part in no choice
    #   train: the rest
    # Returns (universe, train, validation, test).
    u = universe(df)
    rest, test = split(u, N_TEST)
    train, validation = split(rest, N_VALIDATION)
    return u, train, validation, test
