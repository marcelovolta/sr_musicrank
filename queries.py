# All SQL queries used by recommender.py, centralized here so it's easier
# to add/edit more complex queries going forward.

# --- one-time schema migration: drop the old Spanish-named objects from
# earlier versions of this app, so a stale local db.mergenres.db gets
# cleaned up automatically instead of ending up with duplicate tables. ---
DROP_LEGACY_TABLE_INTERACCIONES = "DROP TABLE IF EXISTS interacciones"
DROP_LEGACY_TABLE_VISTAS = "DROP TABLE IF EXISTS vistas"
DROP_LEGACY_VIEW_DISCOS = "DROP VIEW IF EXISTS discos"

CREATE_TABLE_RATINGS = """
    CREATE TABLE IF NOT EXISTS ratings (
        user_id TEXT,
        album_id INTEGER,
        created_at TEXT,
        rating INT,
        PRIMARY KEY (user_id, album_id)
    )
"""

CREATE_TABLE_SEEN = """
    CREATE TABLE IF NOT EXISTS seen (
        user_id TEXT,
        album_id INTEGER,
        created_at datetime,
        PRIMARY KEY (user_id, album_id)
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

COUNT_RATED = """
    SELECT COUNT(*) AS rated_count
    FROM ratings
    WHERE user_id = ? AND rating > 0
"""

COUNT_SEEN = """
    SELECT COUNT(*) AS seen_count
    FROM seen
    WHERE user_id = ?
"""

UNKNOWN_ALBUMS = """
    SELECT *
    FROM album_catalog
    WHERE album_id NOT IN (SELECT album_id FROM ratings WHERE user_id = ?)
    AND album_id NOT IN (SELECT album_id FROM seen WHERE user_id = ?)
"""

INSERT_SEEN = """
    INSERT INTO seen (user_id, album_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
"""

INSERT_RATING = """
    INSERT INTO ratings (user_id, album_id, created_at, rating) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
"""

RECOMMEND_RANDOM = """
    SELECT *
    FROM album_catalog
    WHERE album_id NOT IN (SELECT album_id FROM ratings WHERE user_id = ? AND COALESCE(rating, 0) > 0)
    AND album_id NOT IN (SELECT album_id FROM seen WHERE user_id = ?)
    ORDER BY RANDOM()
    LIMIT {N}
"""

# Community rating comes from user_reviews (real data scraped from RYM), not
# from this app's own local ratings. A minimum of 5 reviews is required so a
# single 5-star vote doesn't dominate the ranking.
RECOMMEND_TOP_N = """
    WITH t1 AS (
    SELECT ur.album_id AS album_id, AVG(ur.rating) AS avg_rating
    FROM user_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT album_id FROM ratings WHERE user_id = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE user_id = ?)
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
    WHERE ur.album_id NOT IN (SELECT album_id FROM ratings WHERE user_id = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT album_id FROM seen WHERE user_id = ?)
    GROUP BY ur.album_id
    ORDER BY rating_count DESC
    LIMIT {N}
)
SELECT * FROM album_catalog WHERE album_id IN (SELECT album_id FROM t1)
"""

RESET_SEEN = "DELETE FROM seen WHERE user_id = ? AND created_at > ?"

RESET_RATINGS = "DELETE FROM ratings WHERE user_id = ? AND created_at > ?"
