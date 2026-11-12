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
        rating REAL,  -- stars, 0.5 to 5 in steps of 0.5 (like user_reviews)
        PRIMARY KEY (username, album_id)
    )
"""

# ratings used to store 1 to 10 in an INT column: it's rebuilt with a REAL
# column and the old values halved, so they become stars like user_reviews
MIGRATE_RATINGS_TO_STARS = [
    "ALTER TABLE ratings RENAME TO ratings_old",
    CREATE_TABLE_RATINGS,
    """INSERT INTO ratings (username, album_id, created_at, rating)
       SELECT username, album_id, created_at, rating / 2.0 FROM ratings_old""",
    "DROP TABLE ratings_old",
]

CREATE_TABLE_SEEN = """
    CREATE TABLE IF NOT EXISTS seen (
        username TEXT,
        album_id INTEGER,
        created_at datetime,
        PRIMARY KEY (username, album_id)
    )
"""

# Every rated review as one table: RYM's (user_reviews) plus this app's
# (ratings), both in stars. When a user rated an album in both places the app
# rating wins, like dataset.read_reviews. Reviews without a rating (NULL or 0)
# don't count. Every algorithm and every stat reads the reviews from here.
DROP_VIEW_ALL_REVIEWS = "DROP VIEW IF EXISTS all_reviews"

CREATE_VIEW_ALL_REVIEWS = """
    CREATE VIEW all_reviews AS
    SELECT ur.username AS username, ur.album_id AS album_id, ur.rating AS rating
    FROM user_reviews AS ur
    WHERE COALESCE(ur.rating, 0) > 0
      AND NOT EXISTS (SELECT 1 FROM ratings AS rt
                      WHERE rt.username = ur.username AND rt.album_id = ur.album_id
                        AND COALESCE(rt.rating, 0) > 0)
    UNION ALL
    SELECT username, album_id, rating
    FROM ratings
    WHERE COALESCE(rating, 0) > 0
"""

# user_reviews has no indexes of its own: every per-user and per-album query
# on all_reviews would scan it whole
CREATE_INDEXES_USER_REVIEWS = [
    "CREATE INDEX IF NOT EXISTS idx_user_reviews_username ON user_reviews (username)",
    "CREATE INDEX IF NOT EXISTS idx_user_reviews_album_id ON user_reviews (album_id)",
]

# review_count/avg_rating per album are kept in album_stats instead of
# aggregated on every request: filled whole at startup and after a reset, and
# refreshed for one album each time it's rated in the app
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
    LEFT JOIN all_reviews AS ur
        ON ur.album_id = al.id
    GROUP BY al.id
"""

REFRESH_ALBUM_STATS = """
    INSERT OR REPLACE INTO album_stats (album_id, review_count, avg_rating)
    SELECT ?, COUNT(rating), AVG(rating) FROM all_reviews WHERE album_id = ?
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

# albums the user rated, in this app or on RYM (each album once)
COUNT_RATED = """
    SELECT COUNT(DISTINCT album_id) AS rated_count FROM all_reviews WHERE username = ?
"""

# a user exists if they have anything in the database: RYM reviews, or
# ratings or seen albums in this app (there's no users table)
USER_EXISTS = """
    SELECT EXISTS (SELECT 1 FROM user_reviews WHERE username = ?)
        OR EXISTS (SELECT 1 FROM ratings WHERE username = ?)
        OR EXISTS (SELECT 1 FROM seen WHERE username = ?) AS user_exists
"""

COUNT_SEEN = """
    SELECT COUNT(*) AS seen_count
    FROM seen
    WHERE username = ?
"""

INSERT_SEEN = """
    INSERT INTO seen (username, album_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
"""

INSERT_RATING = """
    INSERT INTO ratings (username, album_id, created_at, rating) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
"""

RECOMMEND_RANDOM = """
    SELECT *, NULL AS score
    FROM album_catalog
    WHERE album_id NOT IN (SELECT album_id FROM all_reviews WHERE username = ?)
    AND album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    ORDER BY RANDOM()
    LIMIT {N}
"""

# Community rating from all_reviews (RYM plus this app). A minimum of 5 reviews
# is required so a single 5-star vote doesn't dominate the ranking. Albums the
# user already rated (here or on RYM) or has seen are excluded.
RECOMMEND_TOP_N = """
    WITH t1 AS (
    SELECT ur.album_id AS album_id, AVG(ur.rating) AS avg_rating
    FROM all_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT album_id FROM all_reviews WHERE username = ?)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    GROUP BY ur.album_id
    HAVING COUNT(*) >= 5
    ORDER BY avg_rating DESC
    LIMIT {N}
)
SELECT album_catalog.*, t1.avg_rating AS score FROM album_catalog
JOIN t1 ON t1.album_id = album_catalog.album_id
ORDER BY t1.avg_rating DESC
"""

RECOMMEND_MOST_RATED = """
    WITH t1 AS (
    SELECT ur.album_id AS album_id, COUNT(*) AS rating_count
    FROM all_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT album_id FROM all_reviews WHERE username = ?)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
    GROUP BY ur.album_id
    ORDER BY rating_count DESC
    LIMIT {N}
)
SELECT album_catalog.*, t1.rating_count AS score FROM album_catalog
JOIN t1 ON t1.album_id = album_catalog.album_id
ORDER BY t1.rating_count DESC
"""

RESET_SEEN = "DELETE FROM seen WHERE username = ? AND created_at > ?"
RESET_RATINGS = "DELETE FROM ratings WHERE username = ? AND created_at > ?"

# --- per-user profile (panel of the recommendations screen, top reviewers):
# computed live from all_reviews, so ratings given in the app count at once.
# The pre-calculated tables of older versions are dropped.
DROP_LEGACY_PROFILE_TABLES = [
    "DROP TABLE IF EXISTS user_genre_stats",
    "DROP TABLE IF EXISTS user_artist_stats",
    "DROP TABLE IF EXISTS user_top_album",
]

# the 3 most reviewed primary genres (like the genres on the album cards)
TOP_GENRES = """
    SELECT g.name AS genre_name, COUNT(*) AS review_count
    FROM all_reviews AS r
    JOIN album_genres AS ag ON ag.album_id = r.album_id AND ag.kind = 'primary'
    JOIN genres AS g ON g.id = ag.genre_id
    WHERE r.username = ?
    GROUP BY g.id
    ORDER BY review_count DESC, g.name ASC
    LIMIT 3
"""

TOP_ARTISTS = """
    SELECT ar.name AS artist_name, COUNT(*) AS review_count
    FROM all_reviews AS r
    JOIN album_artists AS aa ON aa.album_id = r.album_id
    JOIN artists AS ar ON ar.id = aa.artist_id
    WHERE r.username = ?
    GROUP BY ar.id
    ORDER BY review_count DESC, ar.name ASC
    LIMIT 3
"""

# ties (e.g. two 5.0 ratings) are broken by title so the pick is deterministic
TOP_ALBUM = """
    SELECT r.album_id AS album_id, al.title AS album_title, r.rating AS rating
    FROM all_reviews AS r
    JOIN albums AS al ON al.id = r.album_id
    WHERE r.username = ?
    ORDER BY r.rating DESC, al.title ASC, r.album_id ASC
    LIMIT 1
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
    FROM all_reviews AS ure
    INNER JOIN album_genres AS ag
        ON ure.album_id = ag.album_id
    WHERE ure.username = ?
    GROUP BY ag.genre_id
    HAVING COUNT(*) > 2  -- hyperparameter
),

pref_artists AS (
    SELECT
        aar.artist_id,
        AVG(ure.rating) AS artist_evals_avg,
        COUNT(*) AS artist_eval_count,
        (0.8 * AVG(ure.rating)) + (0.2 * COUNT(*)) AS artist_corr_rating
    FROM all_reviews AS ure
    INNER JOIN album_all_artists AS aar
        ON ure.album_id = aar.album_id
    WHERE ure.username = ?
    GROUP BY aar.artist_id
    HAVING COUNT(*) > 2  -- hyperparameter
),

popular_albums AS (
    SELECT
        album_id,
        AVG(rating) AS avg_rating,
        COUNT(*) AS review_count,
        (0.8 * AVG(rating)) + (0.2 * COUNT(*)) AS corr_rating
    FROM all_reviews
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
WHERE fog.album_id NOT IN (SELECT album_id FROM all_reviews WHERE username = ?)
  AND fog.album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
ORDER BY global_rating DESC, fog.album_id
LIMIT {N}
)
SELECT album_catalog.*, final_select.global_rating AS score FROM album_catalog
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
    count(*) genre_count,
    avg(ur.rating) as avg_rating,
    (0.8 * avg(ur.rating)) + (0.2 * count(*)) as corr_rating_genre
from all_reviews ur
inner join album_genres ag
on ur.album_id  = ag.album_id
where ur.username = ?
group by ag.genre_id
),

fav_artists as (
select
    aa.artist_id,
    count(*) artist_count,
    avg(ur.rating) as avg_rating,
    (0.8 * avg(ur.rating)) + (0.2 * count(*)) as corr_rating_artist
from all_reviews ur
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
where not exists (select 1 from all_reviews r where r.username = ? and r.album_id = al.id)
and not exists (select 1 from seen where seen.username = ? and seen.album_id = al.id)
order by final_rating desc
),

-- fallback pool for when the tunnel_vision tier comes up short of N; excluded
-- from it explicitly (not just by applying the same per-user filters) so an
-- album that qualifies for both tiers can't appear twice in the final list
popular_albums AS (
    SELECT
        album_id,
        AVG(rating) AS avg_rating,
        COUNT(*) AS review_count,
        (0.8 * AVG(rating)) + (0.2 * COUNT(*)) AS corr_rating,
        1 as sorting
    FROM all_reviews
    WHERE album_id NOT IN (SELECT album_id FROM tunnel_vision)
      AND album_id NOT IN (SELECT album_id FROM all_reviews WHERE username = ?)
      AND album_id NOT IN (SELECT album_id FROM seen WHERE username = ?)
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
    ac.*,
    ps.final_rating as score,
    ps.sorting as fallback  -- 1: from the popular albums fallback
from pre_selection as ps
inner join album_catalog as ac
on ps.album_id = ac.album_id
order by ps.sorting asc,
ps.final_rating desc
limit {N}
"""

# --- trained models (models.py, ensemble.py) ---

# albums the user already rated (in this app or on RYM) or has seen
EXCLUDED_ALBUMS = """
    SELECT album_id FROM all_reviews WHERE username = ?
    UNION
    SELECT album_id FROM seen WHERE username = ?
"""

CATALOG_ALBUMS = """
    SELECT * FROM album_catalog WHERE album_id IN ({placeholders})
"""

# one row per album with its attributes for the models (big_tower, the optional
# ones of ease and two_towers, the ensemble's content features): its first
# credited artist, first primary genre, language and release year
TOWER_ALBUM_FEATURES = """
    SELECT
        al.id AS album_id,
        (SELECT aa.artist_id FROM album_artists AS aa WHERE aa.album_id = al.id ORDER BY aa.position LIMIT 1) AS artist,
        (SELECT ag.genre_id FROM album_genres AS ag WHERE ag.album_id = al.id AND ag.kind = 'primary' ORDER BY ag.rowid LIMIT 1) AS genre,
        al.language AS language,
        SUBSTR(al.release_date, -4) AS release_year
    FROM albums AS al
"""
