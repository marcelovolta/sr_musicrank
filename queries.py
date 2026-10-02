# All SQL queries used by recommender.py, centralized here so it's easier
# to add/edit more complex queries going forward.

# --- one-time schema migration: drop the old Spanish-named objects from
# earlier versions of this app, so a stale local db.mergenres.db gets
# cleaned up automatically instead of ending up with duplicate tables. ---
DROP_LEGACY_TABLE_INTERACCIONES = "DROP TABLE IF EXISTS interacciones"
DROP_LEGACY_TABLE_VISTAS = "DROP TABLE IF EXISTS vistas"
DROP_LEGACY_VIEW_DISCOS = "DROP VIEW IF EXISTS discos"

# ratings/seen used to key on user_id; renamed to username so this app's own
# tables use the same identifier as the scraped users/user_reviews tables.
# Renaming in place (rather than drop+recreate) keeps any locally
# accumulated ratings/seen rows intact.
RENAME_RATINGS_USER_ID_TO_USERNAME = "ALTER TABLE ratings RENAME COLUMN user_id TO username"
RENAME_SEEN_USER_ID_TO_USERNAME = "ALTER TABLE seen RENAME COLUMN user_id TO username"

CREATE_TABLE_RATINGS = """
    CREATE TABLE IF NOT EXISTS ratings (
        username TEXT,
        album_id INTEGER,
        created_at TEXT,
        rating INT,
        PRIMARY KEY (username, album_id)
    )
"""

CREATE_TABLE_SEEN = """
    CREATE TABLE IF NOT EXISTS seen (
        username TEXT,
        album_id INTEGER,
        created_at datetime,
        PRIMARY KEY (username, album_id)
    )
"""

# user_reviews is a big, static, scraped table, so review_count/avg_rating
# per album are pre-calculated once into album_stats instead of aggregating
# user_reviews on every recommendation request.
CREATE_TABLE_ALBUM_STATS = """
    CREATE TABLE IF NOT EXISTS album_stats (
        album_id INTEGER PRIMARY KEY REFERENCES albums(id),
        review_count INTEGER NOT NULL,
        avg_rating REAL
    )
"""

POPULATE_ALBUM_STATS = """
    INSERT INTO album_stats (album_id, review_count, avg_rating)
    SELECT
        al.id,
        COUNT(ur.rating),
        AVG(ur.rating)
    FROM albums AS al
    LEFT JOIN user_reviews AS ur
        ON ur.album_id = al.id AND COALESCE(ur.rating, 0) > 0
    GROUP BY al.id
"""

CLEAR_ALBUM_STATS = "DELETE FROM album_stats"

# Views can't be ALTERed in SQLite, and "CREATE VIEW IF NOT EXISTS" would
# silently keep a stale definition around after this query changes, so it's
# always dropped and recreated instead.
DROP_VIEW_ALBUM_CATALOG = "DROP VIEW IF EXISTS album_catalog"

# View that builds the "card" for each album, combining artist(s), primary
# genre(s), pre-calculated review stats, and a cover image fallback when
# there isn't one.
CREATE_VIEW_ALBUM_CATALOG = """
    CREATE VIEW album_catalog AS
    SELECT
        al.id AS album_id,
        al.title AS title,
        (
            SELECT GROUP_CONCAT(ar.name, ', ')
            FROM album_artists AS aa
            JOIN artists AS ar ON ar.id = aa.artist_id
            WHERE aa.album_id = al.id
        ) AS artists,
        (
            SELECT GROUP_CONCAT(g.name, ', ')
            FROM album_genres AS ag
            JOIN genres AS g ON g.id = ag.genre_id
            WHERE ag.album_id = al.id AND ag.kind = 'primary'
        ) AS genres,
        al.release_date AS release_date,
        am.descriptors AS descriptors,
        al.cover_url AS cover_url,
        ast.review_count AS review_count,
        ast.avg_rating AS avg_rating
    FROM albums AS al
    LEFT JOIN album_metadata AS am ON am.album_id = al.id
    LEFT JOIN album_stats AS ast ON ast.album_id = al.id
"""

# A "rated" album is one the user scored either in this app (local ratings
# table) or, when their username matches a real RYM user, one they already
# reviewed on RYM (user_reviews). UNION dedupes albums rated in both places.
COUNT_RATED = """
    SELECT COUNT(*) AS rated_count FROM (
        SELECT album_id FROM ratings WHERE username = ? AND rating > 0
        UNION
        SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0
    )
"""

COUNT_SEEN = """
    SELECT COUNT(*) AS seen_count
    FROM seen
    WHERE username = ?
"""

UNKNOWN_ALBUMS = """
    SELECT *
    FROM album_catalog
    WHERE album_id NOT IN (SELECT album_id FROM ratings WHERE username = ?)
    AND album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    AND album_id NOT IN (SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0)
"""

INSERT_SEEN = """
    INSERT INTO seen (username, album_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
"""

INSERT_RATING = """
    INSERT INTO ratings (username, album_id, created_at, rating) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
"""

RECOMMEND_RANDOM = """
    SELECT *
    FROM album_catalog
    WHERE album_id NOT IN (SELECT album_id FROM ratings WHERE username = ? AND COALESCE(rating, 0) > 0)
    AND album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    AND album_id NOT IN (SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0)
    ORDER BY RANDOM()
    LIMIT {N}
"""

# Community rating comes from user_reviews (real data scraped from RYM), not
# from this app's own local ratings. A minimum of 5 reviews is required so a
# single 5-star vote doesn't dominate the ranking. Albums the user already
# rated locally, has seen, or already reviewed on RYM are excluded.
RECOMMEND_TOP_N = """
    WITH t1 AS (
    SELECT ur.album_id AS album_id, AVG(ur.rating) AS avg_rating
    FROM user_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT album_id FROM ratings WHERE username = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    AND ur.album_id NOT IN (SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0)
    GROUP BY ur.album_id
    HAVING COUNT(*) >= 5
    ORDER BY avg_rating DESC
    LIMIT {N}
)
SELECT album_catalog.* FROM album_catalog
JOIN t1 ON t1.album_id = album_catalog.album_id
ORDER BY t1.avg_rating DESC
"""

RECOMMEND_MOST_RATED = """
    WITH t1 AS (
    SELECT ur.album_id AS album_id, COUNT(*) AS rating_count
    FROM user_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT album_id FROM ratings WHERE username = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    AND ur.album_id NOT IN (SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0)
    GROUP BY ur.album_id
    ORDER BY rating_count DESC
    LIMIT {N}
)
SELECT album_catalog.* FROM album_catalog
JOIN t1 ON t1.album_id = album_catalog.album_id
ORDER BY t1.rating_count DESC
"""

RESET_SEEN = "DELETE FROM seen WHERE username = ? AND created_at > ?"
RESET_RATINGS = "DELETE FROM ratings WHERE username = ? AND created_at > ?"

# --- per-user profile stats, pre-calculated from user_reviews (static, like
# album_stats) instead of aggregated per request. ---

CREATE_TABLE_USER_GENRE_STATS = """
    CREATE TABLE IF NOT EXISTS user_genre_stats (
        username TEXT,
        genre_name TEXT,
        review_count INTEGER NOT NULL,
        rnk INTEGER NOT NULL,
        PRIMARY KEY (username, genre_name)
    )
"""

CLEAR_USER_GENRE_STATS = "DELETE FROM user_genre_stats"

# only the top 3 primary genres per user are kept (matches the genres shown
# on album cards, which are also primary-only)
POPULATE_USER_GENRE_STATS = """
    INSERT INTO user_genre_stats (username, genre_name, review_count, rnk)
    SELECT username, genre_name, review_count, rnk FROM (
        SELECT
            ur.username AS username,
            g.name AS genre_name,
            COUNT(*) AS review_count,
            ROW_NUMBER() OVER (
                PARTITION BY ur.username
                ORDER BY COUNT(*) DESC, g.name ASC
            ) AS rnk
        FROM user_reviews AS ur
        JOIN album_genres AS ag ON ag.album_id = ur.album_id AND ag.kind = 'primary'
        JOIN genres AS g ON g.id = ag.genre_id
        WHERE COALESCE(ur.rating, 0) > 0
        GROUP BY ur.username, g.id
    )
    WHERE rnk <= 3
"""

CREATE_TABLE_USER_ARTIST_STATS = """
    CREATE TABLE IF NOT EXISTS user_artist_stats (
        username TEXT,
        artist_name TEXT,
        review_count INTEGER NOT NULL,
        rnk INTEGER NOT NULL,
        PRIMARY KEY (username, artist_name)
    )
"""

CLEAR_USER_ARTIST_STATS = "DELETE FROM user_artist_stats"

POPULATE_USER_ARTIST_STATS = """
    INSERT INTO user_artist_stats (username, artist_name, review_count, rnk)
    SELECT username, artist_name, review_count, rnk FROM (
        SELECT
            ur.username AS username,
            ar.name AS artist_name,
            COUNT(*) AS review_count,
            ROW_NUMBER() OVER (
                PARTITION BY ur.username
                ORDER BY COUNT(*) DESC, ar.name ASC
            ) AS rnk
        FROM user_reviews AS ur
        JOIN album_artists AS aa ON aa.album_id = ur.album_id
        JOIN artists AS ar ON ar.id = aa.artist_id
        WHERE COALESCE(ur.rating, 0) > 0
        GROUP BY ur.username, ar.id
    )
    WHERE rnk <= 3
"""

CREATE_TABLE_USER_TOP_ALBUM = """
    CREATE TABLE IF NOT EXISTS user_top_album (
        username TEXT PRIMARY KEY,
        album_id INTEGER,
        album_title TEXT,
        rating REAL
    )
"""

CLEAR_USER_TOP_ALBUM = "DELETE FROM user_top_album"

# ties (e.g. two 5.0 ratings) are broken by title so the pick is deterministic
POPULATE_USER_TOP_ALBUM = """
    INSERT INTO user_top_album (username, album_id, album_title, rating)
    SELECT username, album_id, album_title, rating FROM (
        SELECT
            ur.username AS username,
            ur.album_id AS album_id,
            al.title AS album_title,
            ur.rating AS rating,
            ROW_NUMBER() OVER (
                PARTITION BY ur.username
                ORDER BY ur.rating DESC, al.title ASC, ur.album_id ASC
            ) AS rnk
        FROM user_reviews AS ur
        JOIN albums AS al ON al.id = ur.album_id
        WHERE COALESCE(ur.rating, 0) > 0
    )
    WHERE rnk = 1
"""

TOP_GENRES = """
    SELECT genre_name, review_count
    FROM user_genre_stats
    WHERE username = ?
    ORDER BY rnk
"""

TOP_ARTISTS = """
    SELECT artist_name, review_count
    FROM user_artist_stats
    WHERE username = ?
    ORDER BY rnk
"""

TOP_ALBUM = """
    SELECT album_id, album_title, rating
    FROM user_top_album
    WHERE username = ?
"""

RECOMMEND_BALANCED_PROFILE = """
WITH album_all_genres AS (
    SELECT album_id, genre_id
    FROM album_genres
),

album_all_artists AS (
    SELECT album_id, artist_id
    FROM album_artists
),

pref_genres AS (
    SELECT
        ag.genre_id,
        AVG(ure.rating) AS genre_evals_avg,
        COUNT(*) AS genre_evals_count,
        (0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS genre_corr_rating
    FROM user_reviews AS ure
    INNER JOIN album_genres AS ag
        ON ure.album_id = ag.album_id
    WHERE ure.username = ?
      AND COALESCE(ure.rating, 0) > 0
    GROUP BY ag.genre_id
    HAVING COUNT(*) > 2  -- hyperparameter
),

pref_artists AS (
    SELECT
        aar.artist_id,
        AVG(ure.rating) AS artist_evals_avg,
        COUNT(*) AS artist_eval_count,
        (0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS artist_corr_rating
    FROM user_reviews AS ure
    INNER JOIN album_all_artists AS aar
        ON ure.album_id = aar.album_id
    WHERE ure.username = ?
      AND COALESCE(ure.rating, 0) > 0
    GROUP BY aar.artist_id
    HAVING COUNT(*) > 2  -- hyperparameter
),

popular_albums AS (
    SELECT
        album_id,
        AVG(rating) AS avg_rating,
        COUNT(*) AS review_count,
        (0.8 * AVG(rating)) + (0.2 * COUNT(*)) AS corr_rating
    FROM user_reviews
    GROUP BY album_id
    HAVING COUNT(*) > 5
),

fan_out_by_genre AS (
    SELECT
        pal.album_id,
        pal.corr_rating,
        SUM(pge.genre_corr_rating) AS genre_corr_rating
    FROM popular_albums AS pal
    INNER JOIN album_all_genres AS age
        ON pal.album_id = age.album_id
    LEFT JOIN pref_genres AS pge
        ON age.genre_id = pge.genre_id
    GROUP BY pal.album_id, pal.corr_rating
),

fan_out_by_artist AS (
    SELECT
        pal.album_id,
        SUM(par.artist_corr_rating) AS artist_corr_rating
    FROM popular_albums AS pal
    INNER JOIN album_all_artists AS aar
        ON pal.album_id = aar.album_id
    LEFT JOIN pref_artists AS par
        ON aar.artist_id = par.artist_id
    GROUP BY pal.album_id
),

final_select AS (
SELECT
    fog.album_id,
    fog.corr_rating,
    fog.genre_corr_rating,
    foa.artist_corr_rating,
    (0.4 * COALESCE(fog.genre_corr_rating, 0))
      + (0.4 * COALESCE(foa.artist_corr_rating, 0))
      + (0.2 * fog.corr_rating) AS global_rating
FROM fan_out_by_genre AS fog
INNER JOIN fan_out_by_artist AS foa
    ON fog.album_id = foa.album_id
WHERE fog.album_id NOT IN (SELECT album_id FROM ratings WHERE username = ? AND COALESCE(rating, 0) > 0)
  AND fog.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
  AND fog.album_id NOT IN (SELECT album_id FROM user_reviews WHERE username = ? AND COALESCE(rating, 0) > 0)
ORDER BY global_rating DESC, fog.album_id
LIMIT {N}
)
SELECT album_catalog.* FROM album_catalog
JOIN final_select ON final_select.album_id = album_catalog.album_id
ORDER BY final_select.global_rating DESC, final_select.album_id
"""

# Narrower than balanced_profile: an album must match BOTH a genre and an
# artist the user already favors (inner joins, not left joins), falling back
# to generally popular albums (sorting=1) if that intersection comes up
# short of N.
RECOMMEND_TUNNEL_VISION = """
with fav_genres as (
select
    ag.genre_id,
    count(distinct ur.id) genre_count,
    avg(ur.rating) as avg_rating,
    (0.8 * avg(ur.rating)) + (0.2 * count(distinct ur.id)) as corr_rating_genre
from user_reviews ur
inner join album_genres ag
on ur.album_id  = ag.album_id
where ur.username = ?
group by ag.genre_id
),

fav_artists as (
select
    aa.artist_id,
    count(distinct ur.id) artist_count,
    avg(ur.rating) as avg_rating,
    (0.8 * avg(ur.rating)) + (0.2 * count(distinct ur.id)) as corr_rating_artist
from user_reviews ur
inner join album_artists aa
on ur.album_id  = aa.album_id
where ur.username = ?
group by aa.artist_id
),

selected_albums_genre as (
select
    al.id as album_id,
    sum(fg.corr_rating_genre) as global_corr_rating_genre
from albums al
inner join album_genres ag
on al.id = ag.album_id
inner join fav_genres fg
on ag.genre_id = fg.genre_id
group by al.id
),

selected_albums_artist as (
select
    al.id as album_id,
    sum(fa.corr_rating_artist) as global_corr_rating_artist
from albums al
inner join album_artists aa
on al.id = aa.album_id
inner join fav_artists fa
on aa.artist_id = fa.artist_id
group by al.id
),

tunnel_vision as (
select
    al.id as album_id,
    saa.global_corr_rating_artist,
    sag.global_corr_rating_genre,
    saa.global_corr_rating_artist + sag.global_corr_rating_genre as final_rating,
    0 as sorting
from albums al
inner join selected_albums_artist saa
on al.id = saa.album_id
inner join selected_albums_genre sag
on al.id = sag.album_id
where not exists (select 1 from seen where seen.username = ? and seen.album_id = al.id)
and not exists (select 1 from user_reviews ur where ur.username = ? and ur.album_id = al.id)
order by final_rating desc
),

popular_albums AS (
    SELECT
        album_id,
        AVG(rating) AS avg_rating,
        COUNT(*) AS review_count,
        (0.8 * AVG(rating)) + (0.2 * COUNT(*)) AS corr_rating,
        1 as sorting
    FROM user_reviews
    GROUP BY album_id
    HAVING COUNT(*) > 5
),

pre_selection as (
select
    album_id,
    final_rating,
    sorting
from tunnel_vision
union
select
    album_id,
    corr_rating as final_rating,
    sorting
from popular_albums
)

select
    ac.*
from pre_selection as ps
inner join album_catalog as ac
on ps.album_id = ac.album_id
order by ps.sorting asc,
ps.final_rating desc
limit {N}
"""
