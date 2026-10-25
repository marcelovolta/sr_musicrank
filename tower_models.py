import os
import pickle
import sqlite3
import threading
from itertools import islice

import numpy as np
import pandas as pd

import queries
import surprise_models


# Neural network models from the notebooks clase13_Dos_torres_*.ipynb and
# clase13_Una_gran_torre.ipynb. They're trained with Keras (JAX backend), but
# what gets pickled is just the weights as NumPy arrays, and predictions are
# computed with NumPy: Keras and JAX are only needed to train, not to recommend.
#
# - two_towers: as in clase13_Dos_torres_2. A retrieval tower (dot product of the
#   user and album embeddings) picks the 100 best candidates, and the ranking
#   model (embeddings + dense layers) orders them.
# - big_tower: as in clase13_Una_gran_torre. A single ranking model that besides
#   user and album uses the album's artist, primary genre, language and release
#   year. Unlike the books notebook it has no user features: age and gender are
#   empty for every RYM user. It orders all albums.
#
# Unlike the notebooks, they're trained on every rating (no test split), same as
# the Surprise models.
NAMES = ('two_towers', 'big_tower')
TWO_TOWERS_CANDIDATES = 100
RATING_MIN, RATING_MAX = 0.5, 5

_models = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Prediction (NumPy only)
# ---------------------------------------------------------------------------

def _score(model, u, albums):
    # The ranking model's dense layers, computed with NumPy. The first layer is
    # linear over the concatenated user and album features, so its output is the
    # sum of a part that depends only on the user and one that depends only on
    # the album, both calculated when training
    h = np.maximum(model['user_part'][u] + model['album_part'][albums], 0)
    (w2, b2), (w3, b3) = model['layers']
    h = np.maximum(h @ w2 + b2, 0)
    rating = 1 / (1 + np.exp(-(h @ w3 + b3)[:, 0]))  # sigmoid
    return rating * (RATING_MAX - RATING_MIN) + RATING_MIN


def ranking(name, database_file, username, is_candidate):
    # yields the recommended albums from highest to lowest predicted rating.
    # is_candidate(album_id) says whether an album can be recommended; two_towers
    # needs it to pick its 100 candidates among albums the user doesn't know yet
    m = model(name, database_file)

    # users who weren't there at training time have no embedding: use Baseline
    u = m['user_index'].get(username)
    if u is None:
        yield from surprise_models.ranking('baseline', database_file, username)
        return

    if name == 'two_towers':
        affinity = m['retrieval_album'] @ m['retrieval_user'][u]
        candidates = (i for i in np.argsort(-affinity, kind='stable') if is_candidate(m['album_ids'][i]))
        albums = np.array(list(islice(candidates, TWO_TOWERS_CANDIDATES)), dtype=np.int64)
    else:
        albums = np.arange(len(m['album_ids']))

    if len(albums) == 0:
        return
    est = _score(m, u, albums)
    for j in np.argsort(-est, kind='stable'):
        yield m['album_ids'][albums[j]], est[j]


# ---------------------------------------------------------------------------
# Loading and training
# ---------------------------------------------------------------------------

def model(name, database_file):
    # loaded (or trained) the first time it's used and then kept in memory
    with _lock:
        if name not in _models:
            _models[name] = _load_or_train(name, database_file)
        return _models[name]


def _load_or_train(name, database_file):
    # use the saved model if there is one; otherwise train it and save it.
    # To retrain, delete the .pkl file
    path = os.path.join(surprise_models.MODELS_DIR, f'{name}.pkl')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return pickle.load(f)

    m = _train(name, database_file)
    os.makedirs(surprise_models.MODELS_DIR, exist_ok=True)
    with open(path + '.tmp', 'wb') as f:
        pickle.dump(m, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(path + '.tmp', path)
    return m


def _keras():
    # Keras is only imported to train
    os.environ.setdefault('KERAS_BACKEND', 'jax')
    import keras
    keras.utils.set_random_seed(42)
    return keras


def _codes(series):
    # category -> number, like .astype('category').cat.codes in the notebook;
    # nulls get their own code at the end instead of -1
    codes = series.astype('category').cat.codes.to_numpy().astype(np.int64)
    n = int(codes.max()) + 2
    codes[codes < 0] = n - 1
    return codes, n


def _normalize_year(series):
    # as in the notebook: to number, nulls as 0, and scaled between 0 and 1
    x = pd.to_numeric(series, errors='coerce').fillna(0.0).to_numpy(dtype=np.float32)
    return (x - x.min()) / max(x.max() - x.min(), 1.0)


def _train_ranking(keras, user_features, album_features, u, i, y, epochs, batch_size, callbacks=()):
    # Ranking network: one embedding per categorical feature (numeric ones go in
    # as they are), all concatenated and then Dense(256) -> Dense(64) -> Dense(1).
    # *_features: {name: (value per user/album, embedding size or None if numeric)}
    inputs, vectors, x = [], [], {}
    for features, rows in ((user_features, u), (album_features, i)):
        for name, (values, dim) in features.items():
            inp = keras.layers.Input(shape=(1,), name=name)
            inputs.append(inp)
            if dim is None:
                vectors.append(inp)
            else:
                emb = keras.layers.Embedding(int(values.max()) + 1, dim, name=f'emb_{name}')(inp)
                vectors.append(keras.layers.Flatten()(emb))
            x[name] = values[rows].astype(np.float32)

    h = keras.layers.Concatenate()(vectors)
    h = keras.layers.Dense(256, activation='relu', name='dense_1')(h)
    h = keras.layers.Dense(64, activation='relu', name='dense_2')(h)
    output = keras.layers.Dense(1, activation='sigmoid', name='dense_3')(h)

    net = keras.Model(inputs=inputs, outputs=output)
    net.compile(
        loss=keras.losses.MeanSquaredError(),
        metrics=[keras.metrics.RootMeanSquaredError()],
        optimizer=keras.optimizers.Adagrad(learning_rate=0.1),
    )
    net.fit(x, y, epochs=epochs, batch_size=batch_size, callbacks=list(callbacks), verbose=2)

    # split the first layer's matrix into the rows for the user features and
    # the rows for the album features
    w1, b1 = net.get_layer('dense_1').get_weights()
    parts, row = [], 0
    for features in (user_features, album_features):
        blocks = []
        for name, (values, dim) in features.items():
            if dim is None:
                blocks.append(values[:, None].astype(np.float32))
            else:
                blocks.append(net.get_layer(f'emb_{name}').get_weights()[0][values])
        X = np.concatenate(blocks, axis=1)
        parts.append(X @ w1[row:row + X.shape[1]])
        row += X.shape[1]

    return {
        'user_part': parts[0] + b1,
        'album_part': parts[1],
        'layers': [tuple(net.get_layer(n).get_weights()) for n in ('dense_2', 'dense_3')],
    }


def _train(name, database_file):
    keras = _keras()
    conn = sqlite3.connect(database_file)
    df = pd.read_sql(queries.SURPRISE_TRAINING_RATINGS, conn)

    # user and album ids -> positions 0..n-1 (the notebook's translation dictionaries)
    u, usernames = pd.factorize(df['username'])
    i, album_ids = pd.factorize(df['album_id'])
    y = ((df['rating'].to_numpy() - RATING_MIN) / (RATING_MAX - RATING_MIN)).astype(np.float32)
    users = np.arange(len(usernames))
    albums = np.arange(len(album_ids))

    m = {
        'album_ids': [int(a) for a in album_ids],
        'user_index': {username: n for n, username in enumerate(usernames)},
    }

    if name == 'two_towers':
        # retrieval tower (clase13_Dos_torres_2): the score is the dot product of
        # the embeddings, trained to approximate the rating
        retrieval = _retrieval_model(keras, len(usernames), len(album_ids), 256)
        retrieval.compile(loss=keras.losses.MeanSquaredError(), optimizer=keras.optimizers.Adagrad(learning_rate=0.1))
        retrieval.fit({'user': u, 'album': i}, y, epochs=10, batch_size=1024, verbose=2)
        m['retrieval_user'] = retrieval.user_embedding.get_weights()[0]
        m['retrieval_album'] = retrieval.album_embedding.get_weights()[0]

        # ranking model: just the user and album embeddings (size 32)
        m.update(_train_ranking(keras, {'user': (users, 32)}, {'album': (albums, 32)}, u, i, y, epochs=10, batch_size=256))
    else:
        album_df = pd.read_sql(queries.TOWER_ALBUM_FEATURES, conn).set_index('album_id').reindex(album_ids)

        def categorical(series):
            codes, n = _codes(series)
            return codes, min(n // 2 + 1, 50)

        user_features = {
            'user': (users, min(len(users) // 2 + 1, 50)),
        }
        album_features = {
            'album': (albums, min(len(albums) // 2 + 1, 50)),
            'artist': categorical(album_df['artist']),
            'genre': categorical(album_df['genre']),
            'language': categorical(album_df['language']),
            'release_year': (_normalize_year(album_df['release_year']), None),
        }
        # as in the notebook: up to 100 epochs, stopping when the loss doesn't improve for 3 in a row
        stop = keras.callbacks.EarlyStopping(monitor='loss', patience=3)
        m.update(_train_ranking(keras, user_features, album_features, u, i, y, epochs=100, batch_size=1024, callbacks=[stop]))

    conn.close()
    return m


def _retrieval_model(keras, num_users, num_albums, dim):
    class Retrieval(keras.Model):
        # same as the notebook's RetrievalModel without keras-rs's
        # BruteForceRetrieval layer: the search for the best albums is done with
        # NumPy when predicting
        def __init__(self):
            super().__init__()
            self.user_embedding = keras.layers.Embedding(num_users, dim)
            self.album_embedding = keras.layers.Embedding(num_albums, dim)

        def call(self, inputs):
            return keras.ops.sum(self.user_embedding(inputs['user']) * self.album_embedding(inputs['album']), axis=1, keepdims=True)

    return Retrieval()


if __name__ == '__main__':
    # trains and saves whichever models are missing: python tower_models.py
    import recommender
    for n in NAMES:
        model(n, recommender.DATABASE_FILE)
        print(n, 'ready')
