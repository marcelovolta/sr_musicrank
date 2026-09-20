import sqlite3
import os
from datetime import datetime

import queries


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

    cur.execute(queries.CREATE_TABLA_INTERACCIONES)
    cur.execute(queries.CREATE_TABLA_VISTAS)
    cur.execute(queries.CREATE_VISTA_DISCOS)

    conn.commit()
    cur.close()
    conn.close()


inicializar_esquema()


def item_valorados(id_oyente):
    return sql_select(queries.ITEM_VALORADOS, (id_oyente,))


def items_vistos(id_oyente):
    return sql_select(queries.ITEMS_VISTOS, (id_oyente,))

def items_desconocidos(id_oyente):
    return sql_select(queries.ITEMS_DESCONOCIDOS, (id_oyente, id_oyente))


def insertar_vistos(id_oyente, discos):
    for d in discos:
        sql_execute(queries.INSERTAR_VISTO, (id_oyente, d['id_disco'], datetime.now()))


def insertar_valorado(id_oyente, id_disco, rating):
    # TODO: permitir que el oyente actualice una valoración ya existente
    # (hoy ON CONFLICT DO NOTHING ignora la nueva puntuación en silencio)
    fecha = datetime.now()
    sql_execute(queries.INSERTAR_VALORADO, (id_oyente, id_disco, fecha, rating))


def recomendador_random(id_oyente, N=9):
    query = queries.RECOMENDADOR_RANDOM.format(N=N)
    return sql_select(query, (id_oyente, id_oyente))

def recomendador_top_n(id_oyente, N=9):
    query = queries.RECOMENDADOR_TOP_N.format(N=N)
    return sql_select(query, (id_oyente, id_oyente))

def recomendador_mas_valorados(id_oyente, N=9):
    query = queries.RECOMENDADOR_MAS_VALORADOS.format(N=N)
    return sql_select(query, (id_oyente, id_oyente))

def resetear_recientes(id_oyente, fecha_limite='2026-09-01 23:59:59'):
    sql_execute(queries.RESETEAR_VISTAS, (id_oyente, fecha_limite))
    sql_execute(queries.RESETEAR_INTERACCIONES, (id_oyente, fecha_limite))


def recomendar(id_oyente, N=9):
    # return recomendador_random(id_oyente, N)
    # return recomendador_top_n(id_oyente, N)
    rec = recomendador_mas_valorados(id_oyente, N)

    insertar_vistos(id_oyente, rec)
    return rec
