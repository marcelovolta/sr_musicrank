import sqlite3
import os
from datetime import datetime

import queries


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
    cur.execute(queries.CREATE_VIEW_ALBUM_CATALOG)

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

def reset_recent(username, cutoff_date='2026-09-01 23:59:59'):
    sql_execute(queries.RESET_SEEN, (username, cutoff_date))
    sql_execute(queries.RESET_RATINGS, (username, cutoff_date))


def recommend(username, N=9):
    # return recommend_random(username, N)
    # return recommend_top_n(username, N)
    # rec = recommend_most_rated(username, N)
    rec = recommend_balanced_profile(username, N)

    insert_seen(username, rec)
    return rec
