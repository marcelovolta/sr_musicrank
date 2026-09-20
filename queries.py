# Todas las queries SQL usadas por recomendador.py, centralizadas acá para
# que sea más fácil agregar/editar consultas más complejas a futuro.

CREATE_TABLA_INTERACCIONES = """
    CREATE TABLE IF NOT EXISTS interacciones (
        id_oyente TEXT,
        id_disco INTEGER,
        fecha TEXT,
        rating INT,
        PRIMARY KEY (id_oyente, id_disco)
    )
"""

CREATE_TABLA_VISTAS = """
    CREATE TABLE IF NOT EXISTS vistas (
        id_oyente TEXT,
        id_disco INTEGER,
        fecha datetime,
        PRIMARY KEY (id_oyente, id_disco)
    )
"""

# Vista que arma la "ficha" de cada disco combinando artista(s), género(s)
# principal(es) y una imagen de portada de reemplazo cuando no hay una.
CREATE_VISTA_DISCOS = """
    CREATE VIEW IF NOT EXISTS discos AS
    SELECT
        al.id AS id_disco,
        al.title AS titulo,
        (
            SELECT GROUP_CONCAT(ar.name, ', ')
            FROM album_artists AS aa
            JOIN artists AS ar ON ar.id = aa.artist_id
            WHERE aa.album_id = al.id
        ) AS artista,
        (
            SELECT GROUP_CONCAT(g.name, ', ')
            FROM album_genres AS ag
            JOIN genres AS g ON g.id = ag.genre_id
            WHERE ag.album_id = al.id AND ag.kind = 'primary'
        ) AS genero,
        al.release_date AS anio_edicion,
        am.descriptors AS resumen,
        al.cover_url AS img_src
    FROM albums AS al
    LEFT JOIN album_metadata AS am ON am.album_id = al.id
"""

ITEM_VALORADOS = """
    SELECT COUNT(*) AS item_valorados
    FROM interacciones
    WHERE id_oyente = ? AND rating > 0
"""

ITEMS_VISTOS = """
    SELECT COUNT(*) AS items_vistos
    FROM vistas
    WHERE id_oyente = ?
"""

ITEMS_DESCONOCIDOS = """
    SELECT *
    FROM discos
    WHERE id_disco NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ?)
    AND id_disco NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
"""

INSERTAR_VISTO = """
    INSERT INTO vistas (id_oyente, id_disco, fecha) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
"""

INSERTAR_VALORADO = """
    INSERT INTO interacciones (id_oyente, id_disco, fecha, rating) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
"""

RECOMENDADOR_RANDOM = """
    SELECT *
    FROM discos
    WHERE id_disco NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ? AND COALESCE(rating, 0) > 0)
    AND id_disco NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
    ORDER BY RANDOM()
    LIMIT {N}
"""

# El rating de la comunidad sale de user_reviews (datos reales scrapeados de
# RYM), no de las valoraciones locales de esta app. Se pide un mínimo de 5
# reviews para que un solo voto de 5 estrellas no domine el ranking.
RECOMENDADOR_TOP_N = """
    WITH t1 AS (
    SELECT ur.album_id AS id_disco, AVG(ur.rating) AS promedio_rating
    FROM user_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
    GROUP BY ur.album_id
    HAVING COUNT(*) >= 5
    ORDER BY promedio_rating DESC
    LIMIT {N}
)
SELECT * FROM discos WHERE id_disco IN (SELECT id_disco FROM t1)
"""

RECOMENDADOR_MAS_VALORADOS = """
    WITH t1 AS (
    SELECT ur.album_id AS id_disco, COUNT(*) AS valoraciones
    FROM user_reviews AS ur
    WHERE ur.album_id NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ? AND COALESCE(rating, 0) > 0)
    AND ur.album_id NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
    GROUP BY ur.album_id
    ORDER BY valoraciones DESC
    LIMIT {N}
)
SELECT * FROM discos WHERE id_disco IN (SELECT id_disco FROM t1)
"""

RESETEAR_VISTAS = "DELETE FROM vistas WHERE id_oyente = ? AND fecha > ?"

RESETEAR_INTERACCIONES = "DELETE FROM interacciones WHERE id_oyente = ? AND fecha > ?"
