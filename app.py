from flask import Flask, request, render_template, redirect
import recomendador

app = Flask(__name__)

@app.get('/')
def get_index():
    return render_template('login.html')

@app.post('/')
def post_index():
    id_oyente = request.form.get('id_oyente')
    if not id_oyente:
        return render_template('login.html')
    # TODO: Validar que el id_oyente exista en la base de datos
    return redirect(f'/recomendar/{id_oyente}')


@app.get('/reset/<id_oyente>')
def get_reset(id_oyente):
    recomendador.resetear_recientes(id_oyente)
    return redirect(f'/recomendar/{id_oyente}')


@app.get('/recomendar/<id_oyente>')
def get_recomendar(id_oyente):
    discos_recomendados = recomendador.recomendar(id_oyente)
    cant_vistos = recomendador.items_vistos(id_oyente)[0]['items_vistos']
    cant_valorados = recomendador.item_valorados(id_oyente)[0]['item_valorados']
    return render_template('recomendaciones.html', cant_vistos=cant_vistos, cant_valorados=cant_valorados, discos_recomendados=discos_recomendados, id_oyente=id_oyente)


@app.post('/recomendar/<id_oyente>')
def post_recomendar(id_oyente):
    # guardar los ítems puntuados, y volver a recomendar
    # los vistos los guardo en el get_recomendar, porque ahí es donde se muestran
    for id_disco in request.form.keys():
        rating = int(request.form.get(id_disco, 0))
        print(f'El oyente {id_oyente} puntuó el disco {id_disco} con {rating}')
        if rating > 0: # Lo puntuó, entonces lo guardamos en la tabla de interacciones
            recomendador.insertar_valorado(id_oyente, id_disco, rating)

    return redirect(f'/recomendar/{id_oyente}')
