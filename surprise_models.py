import os
import pickle
import sqlite3
import threading

import numpy as np
import pandas as pd
import surprise as sp

import queries


# Algorithms from the Surprise library, with the same hyperparameters as in
# clase09_suprise.ipynb. KNN uses KNNWithMeans, which scored a better RMSE than
# KNNBasic in the notebook. Unlike the notebook it's item-based: RYM users have
# only ~3.7 reviews each, too few to find similar users, and a user-user
# similarity matrix for ~17k users takes ~5 GB of memory.
CONSTRUCTORS = {
    'baseline': lambda: sp.BaselineOnly(verbose=False),
    'knn': lambda: sp.KNNWithMeans(k=40, min_k=5, sim_options={'name': 'pearson', 'user_based': False}, verbose=False),
    'svd': lambda: sp.SVD(n_factors=500, n_epochs=20, random_state=42),
}

# trained models are pickled into this folder, one file per algorithm
MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

_models = {}
_lock = threading.Lock()


def _train(name, database_file):
    conn = sqlite3.connect(database_file)
    df = pd.read_sql(queries.SURPRISE_TRAINING_RATINGS, conn)
    conn.close()

    reader = sp.Reader(rating_scale=(0.5, 5))
    data = sp.Dataset.load_from_df(df[['username', 'album_id', 'rating']], reader) # column order matters
    algo = CONSTRUCTORS[name]()
    algo.fit(data.build_full_trainset())
    return algo


def _load_or_train(name, database_file):
    # use the saved model if there is one; otherwise train it and save it.
    # To retrain (e.g. to pick up new ratings) delete the .pkl file
    path = os.path.join(MODELS_DIR, f'{name}.pkl')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return pickle.load(f)

    algo = _train(name, database_file)
    os.makedirs(MODELS_DIR, exist_ok=True)
    # write to a temp file and then rename it, so an interrupted dump doesn't
    # leave a truncated .pkl behind
    with open(path + '.tmp', 'wb') as f:
        pickle.dump(algo, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(path + '.tmp', path)
    return algo


def model(name, database_file):
    # loaded (or trained) the first time it's used and then kept in memory; the
    # lock stops concurrent requests from loading it more than once
    with _lock:
        if name not in _models:
            _models[name] = _load_or_train(name, database_file)
        return _models[name]


def _estimates(name, algo, username):
    # predicted rating for every album in the trainset, indexed by inner id
    trainset = algo.trainset
    if name == 'knn':
        return np.array([algo.predict(username, trainset.to_raw_iid(i)).est for i in trainset.all_items()])

    try:
        u = trainset.to_inner_uid(username)
    except ValueError:
        u = None

    # Baseline and SVD: the same calculation algo.predict() does, but for all
    # albums at once instead of one predict() call per album. For a user the
    # model doesn't know, their bias and factors count as 0
    est = trainset.global_mean + algo.bi
    if u is not None:
        est = est + algo.bu[u]
        if name == 'svd':
            est = est + algo.qi @ algo.pu[u]
    return est


def ranking(name, database_file, username):
    # yields the albums the model knows, from highest to lowest predicted rating
    algo = model(name, database_file)
    trainset = algo.trainset

    # item-based KNN needs the user's own ratings to compare albums against; for
    # a user it doesn't know it would predict the global mean for every album
    if name == 'knn':
        try:
            trainset.to_inner_uid(username)
        except ValueError:
            yield from ranking('baseline', database_file, username)
            return

    est = _estimates(name, algo, username)
    for i in np.argsort(-est, kind='stable'):
        yield trainset.to_raw_iid(int(i)), est[i]
