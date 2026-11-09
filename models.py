import os
import pickle
import sqlite3
import threading

import numpy as np
import pandas as pd
import scipy.sparse as sps

import queries


# Trained recommendation models (all but the SQL ones in recommender.py). Each
# model is saved as a dict of NumPy arrays with what's needed to score albums;
# predicting needs neither Surprise nor Keras, only NumPy.
#
# - baseline:   BaselineOnly from Surprise. Score = album bias (the user's bias
#               doesn't change the order), the same for every user.
# - svd:        SVD from Surprise (matrix factorization of the rating).
# - ease:       EASE (Steck 2019): linear album-to-album model with a closed-form
#               solution. Optionally with album attributes as pseudo-users.
# - two_towers: user and album embeddings trained with a softmax over the
#               in-batch negatives and logQ correction; score = cosine
#               similarity. Optionally the album tower adds embeddings of its
#               attributes (RYM users have no age or gender: no user attributes).
# - big_tower:  dense network over user, album and the album's attributes
#               (artist, genre, language, year), trained to tell chosen albums
#               from random ones.
#
# Item-based KNN was dropped: predicting album by album is too slow to evaluate
# it with Optuna. The attributes in EASE and two_towers are hyperparameters:
# Optuna decides whether to use them (INITIAL makes sure the configuration
# without attributes is tried too).
#
# The hyperparameters of each one are chosen by optimization.py; here only the
# search space and the training are defined.
NAMES = ('baseline', 'svd', 'ease', 'two_towers', 'big_tower')
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
SEED = 42
RATING_MIN, RATING_MAX = 0.5, 5
ATTRIBUTES = ('artist', 'genre', 'language', 'decade')
MIN_WEIGHT = 1e-3  # weight of an attribute in EASE that amounts to not using it

# First Optuna trials: configurations without attributes, so attributes are
# only chosen if they beat them
INITIAL = {
    'ease': [{'min_album': 2, 'lambda': 100.0, **{f'weight_{a}': MIN_WEIGHT for a in ATTRIBUTES}}],
    'two_towers': [{'min_album': 2, 'positive_rating': 3.5, 'dim': 64, 'temperature': 0.1,
                    'learning_rate': 0.01, 'epochs': 10, 'attributes': False}],
}


# ---------------------------------------------------------------------------
# Search spaces (Optuna)
# ---------------------------------------------------------------------------

def suggest(name, trial):
    # The minimum number of reviews per album is one more hyperparameter of each
    # model (the per-user one isn't: it defines the universe, see dataset.universe).
    p = {'min_album': trial.suggest_int('min_album', 1, 20, log=True)}
    if name == 'baseline':
        p['reg_i'] = trial.suggest_float('reg_i', 0.1, 200, log=True)
        p['reg_u'] = trial.suggest_float('reg_u', 0.1, 200, log=True)
        p['n_epochs'] = trial.suggest_int('n_epochs', 3, 30)
    elif name == 'svd':
        p['n_factors'] = trial.suggest_int('n_factors', 10, 200, log=True)
        p['n_epochs'] = trial.suggest_int('n_epochs', 5, 50)
        p['lr_all'] = trial.suggest_float('lr_all', 1e-3, 0.05, log=True)
        p['reg_all'] = trial.suggest_float('reg_all', 0.005, 0.5, log=True)
    elif name == 'ease':
        p['lambda'] = trial.suggest_float('lambda', 1, 1e4, log=True)
        # how much co-listening sharing each attribute adds (MIN_WEIGHT = not used)
        for a in ATTRIBUTES:
            p[f'weight_{a}'] = trial.suggest_float(f'weight_{a}', MIN_WEIGHT, 10, log=True)
    elif name == 'two_towers':
        p['positive_rating'] = trial.suggest_float('positive_rating', 0.5, 5, step=0.5)
        p['dim'] = trial.suggest_categorical('dim', [32, 64, 128, 256])
        p['temperature'] = trial.suggest_float('temperature', 0.02, 0.5, log=True)
        p['learning_rate'] = trial.suggest_float('learning_rate', 1e-3, 0.05, log=True)
        p['epochs'] = trial.suggest_int('epochs', 3, 30)
        p['attributes'] = trial.suggest_categorical('attributes', [False, True])
    elif name == 'big_tower':
        p['positive_rating'] = trial.suggest_float('positive_rating', 0.5, 5, step=0.5)
        p['negatives'] = trial.suggest_int('negatives', 2, 10)
        p['learning_rate'] = trial.suggest_float('learning_rate', 1e-4, 1e-2, log=True)
        p['epochs'] = trial.suggest_int('epochs', 2, 20)
    return p


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(name, df, params, database, on_epoch_end=None):
    # df: already filtered reviews (username, album_id, rating). Returns the
    # model dict. For the networks, on_epoch_end(epoch, loss, model) is called
    # after each epoch with a function that builds the model at that point (for
    # the learning curves). User and album indices are positions in the sorted
    # id lists; 'listened' is used to leave out what the user already reviewed
    # and to score with EASE, and 'popularity' for users the model doesn't know.
    user_ids = np.array(sorted(df['username'].unique()))
    album_ids = np.array(sorted(df['album_id'].unique()))
    u = np.searchsorted(user_ids, df['username'].to_numpy())
    i = np.searchsorted(album_ids, df['album_id'].to_numpy())
    rating = df['rating'].to_numpy()
    nu, na = len(user_ids), len(album_ids)
    order = np.lexsort((i, u))
    cuts = np.searchsorted(u[order], np.arange(nu + 1))

    m = {
        'name': name,
        'params': params,
        'album_ids': album_ids,
        'user_index': {x: n for n, x in enumerate(user_ids)},
        'listened': [i[order][cuts[n]:cuts[n + 1]] for n in range(nu)],
        'popularity': np.bincount(i, minlength=na).astype(np.float32),
    }
    if name in ('baseline', 'svd'):
        m.update(_train_surprise(name, df, params, user_ids, album_ids))
    elif name == 'ease':
        X = sps.csr_matrix((np.ones(len(u), dtype=np.float32), (u, i)), shape=(nu, na))
        weights = {a: params.get(f'weight_{a}', 0) for a in ATTRIBUTES}
        m['B'] = _ease(X, params['lambda'], album_attributes(database, album_ids) if any(weights.values()) else None, weights)
    elif name in ('two_towers', 'big_tower'):
        notify = None
        if on_epoch_end:
            def notify(epoch, loss, weights):
                # completes the partial model with what isn't network weights
                on_epoch_end(epoch, loss, lambda: {**m, **weights()})
        train_network = _train_two_towers if name == 'two_towers' else _train_big_tower
        m.update(train_network(u, i, rating, user_ids, album_ids, params, database, notify))
    return m


def _train_surprise(name, df, params, user_ids, album_ids):
    # Surprise is used to train (as in class) and only its arrays are kept,
    # reordered by user_ids / album_ids
    import surprise as sp
    data = sp.Dataset.load_from_df(df[['username', 'album_id', 'rating']], sp.Reader(rating_scale=(RATING_MIN, RATING_MAX)))
    ts = data.build_full_trainset()
    album_row = np.array([ts.to_inner_iid(x) for x in album_ids])
    if name == 'baseline':
        algo = sp.BaselineOnly(bsl_options={'method': 'als', 'reg_i': params['reg_i'],
                                            'reg_u': params['reg_u'], 'n_epochs': params['n_epochs']}, verbose=False)
        algo.fit(ts)
        return {'bi': algo.bi[album_row].astype(np.float32)}
    algo = sp.SVD(n_factors=params['n_factors'], n_epochs=params['n_epochs'], lr_all=params['lr_all'],
                  reg_all=params['reg_all'], random_state=SEED)
    algo.fit(ts)
    user_row = np.array([ts.to_inner_uid(x) for x in user_ids])
    return {'bi': algo.bi[album_row].astype(np.float32),
            'pu': algo.pu[user_row].astype(np.float32),
            'qi': algo.qi[album_row].astype(np.float32)}


def album_attributes(database, album_ids):
    # first credited artist, first primary genre, language and release decade
    # of each album, in album_ids order, as integer codes (-1 = missing)
    with sqlite3.connect(database) as conn:
        albums = pd.read_sql(queries.TOWER_ALBUM_FEATURES, conn)
    albums = albums.drop_duplicates('album_id').set_index('album_id').reindex(album_ids)
    year = pd.to_numeric(albums['release_year'], errors='coerce')
    albums['decade'] = (year // 10).where(year > 0)
    return pd.DataFrame({a: albums[a].astype('category').cat.codes.to_numpy() for a in ATTRIBUTES},
                        index=album_ids)


def _ease(X, lam, attributes=None, weights=None):
    # P = (XᵀX + λI)⁻¹,  B = I − P / diag(P),  with diag(B) = 0.
    # X is binary: it counts that the user reviewed the album, not the rating.
    # Attributes (EASE with side information): each value of an attribute (an
    # artist, a genre...) is a pseudo-user that "reviewed" every album with that
    # value, with weight weights[a]. In G = XᵀX that adds weights[a] to every
    # pair of albums sharing the attribute: the model treats them as somewhat
    # co-listened.
    G = (X.T @ X).toarray().astype(np.float64)
    if attributes is not None:
        for a, weight in weights.items():
            codes = attributes[a].to_numpy()
            known = np.flatnonzero(codes >= 0)
            A = sps.csr_matrix((np.ones(len(known)), (known, codes[known])), shape=(len(codes), codes.max() + 1))
            G += weight * (A @ A.T).toarray()
    G[np.diag_indices_from(G)] += lam
    P = np.linalg.inv(G)
    B = -P / np.diag(P)
    B[np.diag_indices_from(B)] = 0
    return B.astype(np.float32)


def _keras():
    # Keras (JAX backend) is only needed to train the towers
    os.environ.setdefault('KERAS_BACKEND', 'jax')
    import keras
    keras.utils.set_random_seed(SEED)
    return keras


def _train_two_towers(u, i, rating, user_ids, album_ids, params, database, on_epoch_end):
    # Each example is a (user, chosen album) pair: chosen = rating >=
    # positive_rating. The loss asks "of the albums in the batch, which one did
    # this user choose": the ones in the other rows are its negatives. Since a
    # popular album shows up more often as a negative, the log of its frequency
    # is subtracted when training (logQ correction); without it the model drifts
    # to the popular ones.
    keras = _keras()
    ops = keras.ops
    nu, na = len(user_ids), len(album_ids)
    positive = rating >= params['positive_rating']
    u, i = u[positive], i[positive]
    frequency = np.bincount(i, minlength=na) + 1
    log_q = np.log(frequency / frequency.sum()).astype(np.float32)
    dim, temperature = params['dim'], params['temperature']
    # With attributes, the vector of an album is the sum of the embeddings of
    # its id, artist, genre, language and decade (missing values share a code of
    # their own). It helps albums with few reviews: they look like others with
    # the same attributes.
    codes = {}
    if params.get('attributes'):
        table = album_attributes(database, album_ids)
        for a in table:
            c = table[a].to_numpy().astype(np.int64)
            codes[a] = np.where(c < 0, c.max() + 1, c)

    class TwoTowers(keras.Model):
        # one embedding table for users and one for albums (and one per attribute)
        def __init__(self):
            # creates the embedding tables
            super().__init__()
            self.user_emb = keras.layers.Embedding(nu, dim)
            self.album_emb = keras.layers.Embedding(na, dim)
            self.attribute_emb = {a: keras.layers.Embedding(int(c.max()) + 1, dim, name=f'emb_{a}') for a, c in codes.items()}
            self.tables = {a: ops.convert_to_tensor(c) for a, c in codes.items()}

        def album_vector(self, album):
            # embedding of the id plus those of its attributes
            v = self.album_emb(album)
            for a, emb in self.attribute_emb.items():
                v = v + emb(ops.take(self.tables[a], album))
            return v

        def call(self, inputs):
            # scores of each user in the batch against each album in the batch
            album = inputs['album']
            a = ops.normalize(self.user_emb(inputs['user']), axis=1)
            b = ops.normalize(self.album_vector(album), axis=1)
            scores = ops.matmul(a, ops.transpose(b)) / temperature
            scores = scores - ops.take(ops.convert_to_tensor(log_q), album)[None, :]
            # the same album in another row is not a real negative
            same = ops.equal(album[:, None], album[None, :])
            other_row = ops.logical_not(ops.eye(ops.shape(album)[0], dtype='bool'))
            return ops.where(ops.logical_and(same, other_row), -1e9, scores)

    def loss(_, scores):
        # softmax: for each row, the right answer is the album in that same row
        right = ops.arange(ops.shape(scores)[0])
        return ops.sparse_categorical_crossentropy(right, scores, from_logits=True)

    def embeddings():
        # full vector of each user and album (id + attributes), normalized: the
        # dot product when predicting is the cosine similarity
        users = model.user_emb.get_weights()[0]
        albums = model.album_emb.get_weights()[0]
        for a, c in codes.items():
            albums = albums + model.attribute_emb[a].get_weights()[0][c]
        return {f'{k}_emb': (w / np.maximum(np.linalg.norm(w, axis=1, keepdims=True), 1e-12)).astype(np.float32)
                for k, w in (('user', users), ('album', albums))}

    model = TwoTowers()
    model.compile(loss=loss, optimizer=keras.optimizers.Adam(learning_rate=params['learning_rate']))
    for epoch in range(1, params['epochs'] + 1):
        h = model.fit({'user': u, 'album': i}, np.zeros(len(u), dtype=np.float32), epochs=1, batch_size=1024, verbose=0)
        if on_epoch_end:
            on_epoch_end(epoch, h.history['loss'][-1], embeddings)
    return embeddings()


def _big_tower_features(database, user_ids, album_ids):
    # {feature: (value per user/album, embedding size or None if numeric)}
    with sqlite3.connect(database) as conn:
        albums = pd.read_sql(queries.TOWER_ALBUM_FEATURES, conn)
    albums = albums.drop_duplicates('album_id').set_index('album_id').reindex(album_ids)

    def categorical(series):
        # missing values get a code of their own
        codes = series.astype('category').cat.codes.to_numpy().astype(np.int64)
        n = int(codes.max()) + 2
        codes[codes < 0] = n - 1
        return codes, min(n // 2 + 1, 50)

    def year(series):
        # year as a number, missing as 0, scaled between 0 and 1
        x = pd.to_numeric(series, errors='coerce').fillna(0.0).to_numpy(dtype=np.float32)
        return (x - x.min()) / max(x.max() - x.min(), 1.0), None

    user_features = {'user': (np.arange(len(user_ids)), min(len(user_ids) // 2 + 1, 50))}
    album_features = {
        'album': (np.arange(len(album_ids)), min(len(album_ids) // 2 + 1, 50)),
        'artist': categorical(albums['artist']),
        'genre': categorical(albums['genre']),
        'language': categorical(albums['language']),
        'release_year': year(albums['release_year']),
    }
    return user_features, album_features


def _train_big_tower(u, i, rating, user_ids, album_ids, params, database, on_epoch_end):
    # The network looks at one (user, album) pair at a time and estimates the
    # probability that the user chooses it. Each positive (rating >=
    # positive_rating) goes with `negatives` random albums the user didn't
    # review, drawn again every epoch (with fixed negatives the network
    # memorizes them).
    keras = _keras()
    user_features, album_features = _big_tower_features(database, user_ids, album_ids)
    na = len(album_ids)
    rng = np.random.default_rng(SEED)
    positive = rating >= params['positive_rating']
    u_pos, i_pos = u[positive], i[positive]
    reviewed = u.astype(np.int64) * na + i

    def with_negatives():
        # the positives plus freshly drawn negatives (once per epoch)
        u_neg = np.repeat(u_pos, params['negatives'])
        i_neg = rng.integers(na, size=len(u_neg))
        free = ~np.isin(u_neg.astype(np.int64) * na + i_neg, reviewed)
        u_neg, i_neg = u_neg[free], i_neg[free]
        y = np.concatenate([np.ones(len(u_pos)), np.zeros(len(u_neg))]).astype(np.float32)
        return np.concatenate([u_pos, u_neg]), np.concatenate([i_pos, i_neg]), y

    # embeddings of each feature concatenated -> 3 dense layers of 256 -> sigmoid
    inputs, vectors = [], []
    for features in (user_features, album_features):
        for name, (values, dim) in features.items():
            inp = keras.layers.Input(shape=(1,), name=name)
            inputs.append(inp)
            if dim is None:
                vectors.append(inp)
            else:
                emb = keras.layers.Embedding(int(values.max()) + 1, dim, name=f'emb_{name}')(inp)
                vectors.append(keras.layers.Flatten()(emb))
    h = keras.layers.Concatenate()(vectors)
    for n in (1, 2, 3):
        h = keras.layers.Dense(256, activation='relu', name=f'dense_{n}')(h)
    output = keras.layers.Dense(1, activation='sigmoid', name='output')(h)
    net = keras.Model(inputs=inputs, outputs=output)
    net.compile(loss=keras.losses.BinaryCrossentropy(), optimizer=keras.optimizers.Adam(learning_rate=params['learning_rate']))

    def weights():
        # The first layer is linear over [user features, album features]: the
        # part of each one is precomputed, and predicting only adds them (much
        # faster than passing every pair through the network)
        w1, b1 = net.get_layer('dense_1').get_weights()
        parts, row = [], 0
        for features in (user_features, album_features):
            blocks = [values[:, None].astype(np.float32) if dim is None
                      else net.get_layer(f'emb_{name}').get_weights()[0][values]
                      for name, (values, dim) in features.items()]
            X = np.concatenate(blocks, axis=1)
            parts.append(X @ w1[row:row + X.shape[1]])
            row += X.shape[1]
        return {
            'user_part': (parts[0] + b1).astype(np.float32),
            'album_part': parts[1].astype(np.float32),
            'layers': [tuple(w.astype(np.float32) for w in net.get_layer(n).get_weights())
                       for n in ('dense_2', 'dense_3', 'output')],
        }

    for epoch in range(1, params['epochs'] + 1):
        uu, ii, y = with_negatives()
        x = {name: values[rows].astype(np.float32)
             for features, rows in ((user_features, uu), (album_features, ii))
             for name, (values, _) in features.items()}
        h = net.fit(x, y, epochs=1, batch_size=1024, verbose=0)
        if on_epoch_end:
            on_epoch_end(epoch, h.history['loss'][-1], weights)
    return weights()


# ---------------------------------------------------------------------------
# Prediction (NumPy only)
# ---------------------------------------------------------------------------

def scores(m, users):
    # score of each album of the model for each user (internal indices): a
    # len(users) × albums matrix. Higher = more recommendable
    users = np.asarray(users)
    name = m['name']
    if name == 'baseline':
        return np.tile(m['bi'], (len(users), 1))
    if name == 'svd':
        return m['bi'] + m['pu'][users] @ m['qi'].T
    if name == 'ease':
        na = len(m['album_ids'])
        rows = [m['listened'][u] for u in users]
        X = sps.csr_matrix((np.ones(sum(map(len, rows)), dtype=np.float32), np.concatenate(rows),
                            np.cumsum([0] + [len(r) for r in rows])), shape=(len(users), na))
        return np.asarray(X @ m['B'])
    if name == 'two_towers':
        return m['user_emb'][users] @ m['album_emb'].T
    # big_tower: the dense layers in batches, to avoid huge matrices
    na = len(m['album_ids'])
    out = np.empty((len(users), na), dtype=np.float32)
    batch = max(1, 250_000 // na)
    *hidden, (w_out, b_out) = m['layers']
    for start in range(0, len(users), batch):
        us = users[start:start + batch]
        h = np.maximum(m['user_part'][us][:, None, :] + m['album_part'][None, :, :], 0).reshape(-1, m['album_part'].shape[1])
        for w, b in hidden:
            h = np.maximum(h @ w + b, 0)
        out[start:start + batch] = (h @ w_out + b_out).reshape(len(us), na)  # logit: same order as the sigmoid
    return out


_models = {}
_lock = threading.Lock()


def save(m):
    # saves the model in models/<name>.pkl and drops the in-memory copy
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, f"{m['name']}.pkl")
    with open(path + '.tmp', 'wb') as f:
        pickle.dump(m, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(path + '.tmp', path)  # an interrupted dump doesn't leave a broken .pkl
    with _lock:
        _models.pop(m['name'], None)
    return path


def model(name):
    # loaded the first time it's used and then kept in memory. Models aren't
    # trained on demand: run pipeline.py
    with _lock:
        if name not in _models:
            path = os.path.join(MODELS_DIR, f'{name}.pkl')
            if not os.path.exists(path):
                raise FileNotFoundError(f'Missing {path}: run python pipeline.py')
            with open(path, 'rb') as f:
                _models[name] = pickle.load(f)
        return _models[name]


def knows_user(name, username):
    # False if the user wasn't in the model's universe when it was trained
    return username in model(name)['user_index']


def ranking(name, username):
    # albums of the model from highest to lowest score. To a user it doesn't
    # know it gives the model's most reviewed albums (in the cascades those
    # users go to tunnel_vision)
    m = model(name)
    u = m['user_index'].get(username)
    s = m['popularity'] if u is None else scores(m, [u])[0]
    for j in np.argsort(-s, kind='stable'):
        yield m['album_ids'][j], s[j]
