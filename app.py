from flask import Flask, request, render_template, redirect
import recommender

app = Flask(__name__)

@app.get('/')
def get_index():
    return render_template('login.html')

@app.post('/')
def post_index():
    user_id = request.form.get('user_id')
    if not user_id:
        return render_template('login.html')
    # TODO: validate that user_id exists in the database
    return redirect(f'/recommend/{user_id}')


@app.get('/reset/<user_id>')
def get_reset(user_id):
    recommender.reset_recent(user_id)
    return redirect(f'/recommend/{user_id}')


@app.get('/recommend/<user_id>')
def get_recommend(user_id):
    recommended_albums = recommender.recommend(user_id)
    seen_count = recommender.count_seen(user_id)[0]['seen_count']
    rated_count = recommender.count_rated(user_id)[0]['rated_count']
    return render_template('recommendations.html', seen_count=seen_count, rated_count=rated_count, recommended_albums=recommended_albums, user_id=user_id)


@app.post('/recommend/<user_id>')
def post_recommend(user_id):
    # save the rated items, then recommend again
    # seen items are saved in get_recommend, since that's where they're shown
    for album_id in request.form.keys():
        rating = int(request.form.get(album_id, 0))
        print(f'User {user_id} rated album {album_id} with {rating}')
        if rating > 0: # it was rated, so we save it in the ratings table
            recommender.insert_rating(user_id, album_id, rating)

    return redirect(f'/recommend/{user_id}')
