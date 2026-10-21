import sqlite3
import os
from datetime import datetime
from itertools import islice

import queries
import surprise_models


DATABASE_FILE = os.path.dirname(__file__) + '/data/mergenres.db'

def sql_select(query, params=()):
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # so fetchall returns rows we can index by column name
    cur = conn.cursor()

    rec = cur.execute(query, params).fetchall()

    cur.close()
    conn.close()

    return rec


def sql_execute(query, params=()):
    conn = sqlite3.connect(DATABASE_FILE)
    cur = conn.cursor()

    cur.execute(query, params)

    conn.commit()
    cur.close()
    conn.close()


def _has_column(cur, table, column):
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
    return sql_select(queries.COUNT_RATED, (username, username))


def count_seen(username):
    return sql_select(queries.COUNT_SEEN, (username,))

def unknown_albums(username):
    return sql_select(queries.UNKNOWN_ALBUMS, (username, username, username))


def top_genres(username):
    return sql_select(queries.TOP_GENRES, (username,))


def top_artists(username):
    return sql_select(queries.TOP_ARTISTS, (username,))


def top_album(username):
    rows = sql_select(queries.TOP_ALBUM, (username,))
    return rows[0] if rows else None


def insert_seen(username, albums):
    for album in albums:
        sql_execute(queries.INSERT_SEEN, (username, album['album_id'], datetime.now()))


def insert_rating(username, album_id, rating):
    # TODO: allow the user to update an existing rating
    # (today ON CONFLICT DO NOTHING silently ignores the new rating)
    created_at = datetime.now()
    sql_execute(queries.INSERT_RATING, (username, album_id, created_at, rating))


def recommend_random(username, N=9):
    query = queries.RECOMMEND_RANDOM.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_top_n(username, N=9):
    query = queries.RECOMMEND_TOP_N.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_most_rated(username, N=9):
    query = queries.RECOMMEND_MOST_RATED.format(N=N)
    return sql_select(query, (username, username, username))

def recommend_balanced_profile(username, N=9):
    query = queries.RECOMMEND_BALANCED_PROFILE.format(N=N)
    # order: pref_genres username, pref_artists username, then the
    # final_select exclusions (ratings, seen, user_reviews)
    return sql_select(query, (username, username, username, username, username))

def recommend_tunnel_vision(username, N=9):
    query = queries.RECOMMEND_TUNNEL_VISION.format(N=N)
    # order: fav_genres username, fav_artists username, then the
    # tunnel_vision exclusions (seen, user_reviews, ratings), then the
    # popular_albums fallback's own exclusions (ratings, seen, user_reviews)
    return sql_select(query, (username,) * 8)

def recommend_surprise(name, username, N=9):
    # the N albums with the highest rating predicted by the Surprise model,
    # leaving out the ones the user already rated or has seen
    excluded = {r['album_id'] for r in sql_select(queries.SURPRISE_EXCLUDED_ALBUMS, (username,) * 3)}

    ranking = surprise_models.ranking(name, DATABASE_FILE, username)
    top = list(islice((album_id for album_id, _ in ranking if album_id not in excluded), N))
    if not top:
        return []

    query = queries.SURPRISE_CATALOG_ALBUMS.format(placeholders=', '.join('?' * len(top)))
    albums = sql_select(query, top)
    position = {album_id: i for i, album_id in enumerate(top)}
    return sorted(albums, key=lambda r: position[r['album_id']])

def recommend_baseline(username, N=9):
    return recommend_surprise('baseline', username, N)

def recommend_knn(username, N=9):
    return recommend_surprise('knn', username, N)

def recommend_svd(username, N=9):
    return recommend_surprise('svd', username, N)

def reset_recent(username, cutoff_date='2026-09-01 23:59:59'):
    sql_execute(queries.RESET_SEEN, (username, cutoff_date))
    sql_execute(queries.RESET_RATINGS, (username, cutoff_date))


ALGORITHMS = {
    'random': recommend_random,
    'top_n': recommend_top_n,
    'most_rated': recommend_most_rated,
    'balanced': recommend_balanced_profile,
    'tunnel_vision': recommend_tunnel_vision,
    'baseline': recommend_baseline,
    'knn': recommend_knn,
    'svd': recommend_svd,
}
DEFAULT_ALGORITHM = 'balanced'


def recommend(username, algorithm=DEFAULT_ALGORITHM, N=9):
    recommend_fn = ALGORITHMS.get(algorithm, ALGORITHMS[DEFAULT_ALGORITHM])
    rec = recommend_fn(username, N)

    insert_seen(username, rec)
    return rec
