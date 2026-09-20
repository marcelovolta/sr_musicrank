import sqlite3
import os
from datetime import datetime


DATABASE_FILE = os.path.dirname(__file__) + '/data/mergenres.db'

def sql_select(query, params=()):
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # esto es para que devuelva regisros en el fetchall
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


def inicializar_esquema():
    # mergenres.db es un dump de scraping de RYM: no trae tablas para trackear
    # a los oyentes de esta app, así que las creamos si todavía no existen.
    conn = sqlite3.connect(DATABASE_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS interacciones (
            id_oyente TEXT,
            id_disco INTEGER,
            fecha TEXT,
            rating INT,
            PRIMARY KEY (id_oyente, id_disco)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vistas (
            id_oyente TEXT,
            id_disco INTEGER,
            fecha datetime,
            PRIMARY KEY (id_oyente, id_disco)
        )
    """)

    # Vista que arma la "ficha" de cada disco combinando artista(s), género(s)
    # principal(es) y una imagen de portada de reemplazo cuando no hay una.
    cur.execute("""
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
    """)

    conn.commit()
    cur.close()
    conn.close()


inicializar_esquema()


def item_valorados(id_oyente):
    query = """
        SELECT COUNT(*) AS item_valorados
        FROM interacciones
        WHERE id_oyente = ? AND rating > 0
    """
    return sql_select(query, (id_oyente,))


def items_vistos(id_oyente):
    query = """
        SELECT COUNT(*) AS items_vistos
        FROM vistas
        WHERE id_oyente = ?
    """
    return sql_select(query, (id_oyente,))

def items_desconocidos(id_oyente):
    query = """
        SELECT *
        FROM discos
        WHERE id_disco NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ?)
        AND id_disco NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
    """
    return sql_select(query, (id_oyente, id_oyente))


def insertar_vistos(id_oyente, discos):
    query="""
        INSERT INTO vistas (id_oyente, id_disco, fecha) VALUES (?, ?, ?) ON CONFLICT DO NOTHING
    """

    for d in discos:
        sql_execute(query, (id_oyente, d['id_disco'], datetime.now()))


def insertar_valorado(id_oyente, id_disco, rating):
    # TODO: permitir que el oyente actualice una valoración ya existente
    # (hoy ON CONFLICT DO NOTHING ignora la nueva puntuación en silencio)
    fecha = datetime.now()
    query="""
        INSERT INTO interacciones (id_oyente, id_disco, fecha, rating) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
    """
    sql_execute(query, (id_oyente, id_disco, fecha, rating))


def recomendador_random(id_oyente, N=9):
    query = f"""
        SELECT *
        FROM discos
        WHERE id_disco NOT IN (SELECT id_disco FROM interacciones WHERE id_oyente = ? AND COALESCE(rating, 0) > 0)
        AND id_disco NOT IN (SELECT id_disco FROM vistas WHERE id_oyente = ?)
        ORDER BY RANDOM()
        LIMIT {N}
    """
    return sql_select(query, (id_oyente, id_oyente))

def recomendador_top_n(id_oyente, N=9):
    # El rating de la comunidad sale de user_reviews (datos reales scrapeados
    # de RYM), no de las valoraciones locales de esta app. Se pide un mínimo
    # de 5 reviews para que un solo voto de 5 estrellas no domine el ranking.
    query = f"""
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
    return sql_select(query, (id_oyente, id_oyente))

def recomendador_mas_valorados(id_oyente, N=9):
    query = f"""
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
    return sql_select(query, (id_oyente, id_oyente))

def resetear_recientes(id_oyente, fecha_limite='2026-09-01 23:59:59'):
    sql_execute("DELETE FROM vistas WHERE id_oyente = ? AND fecha > ?", (id_oyente, fecha_limite))
    sql_execute("DELETE FROM interacciones WHERE id_oyente = ? AND fecha > ?", (id_oyente, fecha_limite))


def recomendar(id_oyente, N=9):
    # return recomendador_random(id_oyente, N)
    # return recomendador_top_n(id_oyente, N)
    rec = recomendador_mas_valorados(id_oyente, N)

    insertar_vistos(id_oyente, rec)
    return rec
