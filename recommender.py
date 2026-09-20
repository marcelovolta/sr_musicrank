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


def initialize_schema():
    # mergenres.db is a scraped RYM dump: it has no tables to track this
    # app's own users, so we create them if they don't exist yet.
    conn = sqlite3.connect(DATABASE_FILE)
    cur = conn.cursor()

    cur.execute(queries.DROP_LEGACY_TABLE_INTERACCIONES)
    cur.execute(queries.DROP_LEGACY_TABLE_VISTAS)
    cur.execute(queries.DROP_LEGACY_VIEW_DISCOS)

    cur.execute(queries.CREATE_TABLE_RATINGS)
    cur.execute(queries.CREATE_TABLE_SEEN)
    cur.execute(queries.CREATE_VIEW_ALBUM_CATALOG)

    conn.commit()
    cur.close()
    conn.close()


initialize_schema()


def count_rated(user_id):
    return sql_select(queries.COUNT_RATED, (user_id,))


def count_seen(user_id):
    return sql_select(queries.COUNT_SEEN, (user_id,))

def unknown_albums(user_id):
    return sql_select(queries.UNKNOWN_ALBUMS, (user_id, user_id))


def insert_seen(user_id, albums):
    for album in albums:
        sql_execute(queries.INSERT_SEEN, (user_id, album['album_id'], datetime.now()))


def insert_rating(user_id, album_id, rating):
    # TODO: allow the user to update an existing rating
    # (today ON CONFLICT DO NOTHING silently ignores the new rating)
    created_at = datetime.now()
    sql_execute(queries.INSERT_RATING, (user_id, album_id, created_at, rating))


def recommend_random(user_id, N=9):
    query = queries.RECOMMEND_RANDOM.format(N=N)
    return sql_select(query, (user_id, user_id))

def recommend_top_n(user_id, N=9):
    query = queries.RECOMMEND_TOP_N.format(N=N)
    return sql_select(query, (user_id, user_id))

def recommend_most_rated(user_id, N=9):
    query = queries.RECOMMEND_MOST_RATED.format(N=N)
    return sql_select(query, (user_id, user_id))

def reset_recent(user_id, cutoff_date='2026-09-01 23:59:59'):
    sql_execute(queries.RESET_SEEN, (user_id, cutoff_date))
    sql_execute(queries.RESET_RATINGS, (user_id, cutoff_date))


def recommend(user_id, N=9):
    # return recommend_random(user_id, N)
    # return recommend_top_n(user_id, N)
    rec = recommend_most_rated(user_id, N)

    insert_seen(user_id, rec)
    return rec
