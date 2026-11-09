import sqlite3
import os
from datetime import datetime
from itertools import islice

import ensemble
import models
import queries


DATABASE_FILE = os.path.dirname(__file__) + '/data/mergenres.db'

def sql_select(query, params=()):
    # runs a SELECT on the app's database and returns its rows
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # so fetchall returns rows we can index by column name
    cur = conn.cursor()

    rec = cur.execute(query, params).fetchall()

    cur.close()
    conn.close()

    return rec


def sql_execute(query, params=()):
    # runs a write (INSERT, DELETE...) on the app's database and commits it
    conn = sqlite3.connect(DATABASE_FILE)
    cur = conn.cursor()

    cur.execute(query, params)

    conn.commit()
    cur.close()
    conn.close()


def _has_column(cur, table, column):
    # whether a table has a column (for the migration of older databases)
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def initialize_schema():
    # mergenres.db is a scraped RYM dump: it has no tables to track this
    # app's own users, so we create them if they don't exist yet.
    conn = sqlite3.connect(DATABASE_FILE)
    cur = conn.cursor()

    cur.execute(queries.DROP_LEGACY_TABLE_INTERACCIONES)
    cur.execute(queries.DROP_LEGACY_TABLE_VISTAS)
    cur.execute(queries.DROP_LEGACY_VIEW_DISCOS)

    # older local dbs still key ratings/seen on user_id: rename in place so
    # existing rows survive the switch to username
    if _has_column(cur, 'ratings', 'user_id'):
        cur.execute(queries.RENAME_RATINGS_USER_ID_TO_USERNAME)
    if _has_column(cur, 'seen', 'user_id'):
        cur.execute(queries.RENAME_SEEN_USER_ID_TO_USERNAME)

    cur.execute(queries.CREATE_TABLE_RATINGS)
    cur.execute(queries.CREATE_TABLE_SEEN)

    # user_reviews is static, so review_count/avg_rating are calculated once
    # here rather than aggregated on every recommendation request
    cur.execute(queries.CREATE_TABLE_ALBUM_STATS)
    cur.execute(queries.CLEAR_ALBUM_STATS)
    cur.execute(queries.POPULATE_ALBUM_STATS)

    cur.execute(queries.DROP_VIEW_ALBUM_CATALOG)
    cur.execute(queries.CREATE_VIEW_ALBUM_CATALOG)

    # per-user profile stats (top genres/artists, highest-rated album), same
    # pre-calculate-once approach as album_stats
    cur.execute(queries.CREATE_TABLE_USER_GENRE_STATS)
    cur.execute(queries.CLEAR_USER_GENRE_STATS)
    cur.execute(queries.POPULATE_USER_GENRE_STATS)

    cur.execute(queries.CREATE_TABLE_USER_ARTIST_STATS)
    cur.execute(queries.CLEAR_USER_ARTIST_STATS)
    cur.execute(queries.POPULATE_USER_ARTIST_STATS)

    cur.execute(queries.CREATE_TABLE_USER_TOP_ALBUM)
    cur.execute(queries.CLEAR_USER_TOP_ALBUM)
    cur.execute(queries.POPULATE_USER_TOP_ALBUM)

    conn.commit()
    cur.close()
    conn.close()


initialize_schema()


def count_rated(username):
    # albums the user rated, in this app or on RYM (also decides the cascades' branch)
    return sql_select(queries.COUNT_RATED, (username, username))


def count_seen(username):
    # albums this app already showed the user
    return sql_select(queries.COUNT_SEEN, (username,))

def top_genres(username):
    # the user's 3 most reviewed primary genres (profile panel)
    return sql_select(queries.TOP_GENRES, (username,))


def top_artists(username):
    # the user's 3 most reviewed artists (profile panel)
    return sql_select(queries.TOP_ARTISTS, (username,))


def top_album(username):
    # the user's highest rated album, or None (profile panel)
    rows = sql_select(queries.TOP_ALBUM, (username,))
    return rows[0] if rows else None


def insert_seen(username, albums):
    # records the shown albums as seen, so they aren't recommended again
    for album in albums:
        sql_execute(queries.INSERT_SEEN, (username, album['album_id'], datetime.now()))


def insert_rating(username, album_id, rating):
    # saves a rating given in the app (1 to 10)
    # TODO: allow the user to update an existing rating
    # (today ON CONFLICT DO NOTHING silently ignores the new rating)
    created_at = datetime.now()
    sql_execute(queries.INSERT_RATING, (username, album_id, created_at, rating))


def recommend_random(username, N=9):
    # N random albums the user hasn't rated or seen
    query = queries.RECOMMEND_RANDOM.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_top_n(username, N=9):
    # the N albums with the best RYM average (at least 5 reviews)
    query = queries.RECOMMEND_TOP_N.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_most_rated(username, N=9):
    # the N most reviewed albums on RYM
    query = queries.RECOMMEND_MOST_RATED.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_balanced_profile(username, N=9):
    # N albums by genre and artist affinity plus the album's rating (see queries.py)
    query = queries.RECOMMEND_BALANCED_PROFILE.format(N=N)
    # order: pref_genres username, pref_artists username, then the
    # final_select exclusions (ratings, seen, user_reviews)
    return sql_select(query, (username, username, username, username, username))

def recommend_tunnel_vision(username, N=9):
    # N albums matching both a genre and an artist the user reviewed, then popular ones
    query = queries.RECOMMEND_TUNNEL_VISION.format(N=N)
    # order: fav_genres username, fav_artists username, then the
    # tunnel_vision exclusions (seen, user_reviews, ratings), then the
    # popular_albums fallback's own exclusions (ratings, seen, user_reviews)
    return sql_select(query, (username,) * 8)

def _recommend_from_ranking(username, N, ranking):
    # the first N albums of a ranking of (album_id, score) pairs, best first,
    # leaving out the ones the user already rated or has seen
    excluded = {r['album_id'] for r in sql_select(queries.EXCLUDED_ALBUMS, (username,) * 3)}
    top = list(islice((album_id for album_id, _ in ranking if album_id not in excluded), N))
    if not top:
        return []

    query = queries.CATALOG_ALBUMS.format(placeholders=', '.join('?' * len(top)))
    albums = sql_select(query, [int(a) for a in top])
    position = {album_id: i for i, album_id in enumerate(top)}
    return sorted(albums, key=lambda r: position[r['album_id']])


MODEL_NAMES = (*models.NAMES, 'ensemble')
COLD_START = 2  # in the cascades, users with up to 2 reviews get top_n


def _ranking(name, username):
    # ranking of a trained model; the ensemble lives in its own module
    return ensemble.ranking(username) if name == 'ensemble' else models.ranking(name, username)


def _knows_user(name, username):
    # whether the model has the user (if not, the cascades use tunnel_vision)
    return ensemble.knows_user(username) if name == 'ensemble' else models.knows_user(name, username)


def recommend_model(name):
    # recommender with one of the trained models (models.py or ensemble.py)
    def recommend_fn(username, N=9):
        # the first N of the model's ranking, without what was already rated or seen
        return _recommend_from_ranking(username, N, _ranking(name, username))
    return recommend_fn


def recommend_average(username, N=9):
    # the ensemble's candidates ordered by the simple average of the
    # standardized scores, instead of LightGBM
    return _recommend_from_ranking(username, N, ensemble.ranking(username, average=True))


def recommend_cascade(name):
    # Chooses the algorithm by how well the user is known: cold start (up to
    # COLD_START reviews) with top_n; if the model doesn't know the user (not
    # in its universe: fewer than dataset.MIN_USER_FINAL reviews when it was
    # trained) with tunnel_vision, which can recommend any album; the rest with
    # the model
    model_fn = recommend_model(name)
    def recommend_fn(username, N=9):
        # the cascade for one user
        if count_rated(username)[0]['rated_count'] <= COLD_START:
            return recommend_top_n(username, N)
        if not _knows_user(name, username):
            return recommend_tunnel_vision(username, N)
        return model_fn(username, N)
    return recommend_fn


def reset_recent(username, cutoff_date='2026-09-01 23:59:59'):
    # deletes what the user saw and rated in the app after the cutoff date
    sql_execute(queries.RESET_SEEN, (username, cutoff_date))
    sql_execute(queries.RESET_RATINGS, (username, cutoff_date))


ALGORITHMS = {
    'random': recommend_random,
    'top_n': recommend_top_n,
    'most_rated': recommend_most_rated,
    'balanced': recommend_balanced_profile,
    'tunnel_vision': recommend_tunnel_vision,
    **{name: recommend_model(name) for name in MODEL_NAMES},
    'average': recommend_average,
    **{f'cascade_{name}': recommend_cascade(name) for name in MODEL_NAMES},
}
DEFAULT_ALGORITHM = 'balanced'


def recommend(username, algorithm=DEFAULT_ALGORITHM, N=9):
    # recommends with the chosen algorithm and records the albums as seen
    recommend_fn = ALGORITHMS.get(algorithm, ALGORITHMS[DEFAULT_ALGORITHM])
    rec = recommend_fn(username, N)

    insert_seen(username, rec)
    return rec
