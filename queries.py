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

# View that builds the "card" for each album, combining artist(s), primary
# genre(s), and a cover image fallback when there isn't one.
CREATE_VIEW_ALBUM_CATALOG = """
    CREATE VIEW IF NOT EXISTS album_catalog AS
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
        al.cover_url AS cover_url
    FROM albums AS al
    LEFT JOIN album_metadata AS am ON am.album_id = al.id
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
SELECT * FROM album_catalog WHERE album_id IN (SELECT album_id FROM t1)
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
SELECT * FROM album_catalog WHERE album_id IN (SELECT album_id FROM t1)
"""

RESET_SEEN = "DELETE FROM seen WHERE username = ? AND created_at > ?"
RESET_RATINGS = "DELETE FROM ratings WHERE username = ? AND created_at > ?"

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
SELECT * FROM album_catalog WHERE album_id IN (SELECT album_id FROM final_select)
"""
