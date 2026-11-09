from flask import Flask, request, render_template, redirect
import recommender

app = Flask(__name__)

@app.get('/')
def get_index():
    # login screen: username and algorithm
    return render_template('login.html')

@app.post('/')
def post_index():
    # takes the chosen username and algorithm and goes to their recommendations
    username = request.form.get('username')
    algorithm = request.form.get('algorithm', recommender.DEFAULT_ALGORITHM)
    if not username:
        return render_template('login.html')
    # TODO: validate that username exists in the database
    return redirect(f'/recommend/{username}/{algorithm}')


@app.get('/reset/<username>/<algorithm>')
def get_reset(username, algorithm):
    # deletes what the user saw and rated in the app, and recommends again
    recommender.reset_recent(username)
    return redirect(f'/recommend/{username}/{algorithm}')


@app.get('/recommend/<username>/<algorithm>')
def get_recommend(username, algorithm):
    # shows the recommendations (recording them as seen) and the user's profile
    recommended_albums = recommender.recommend(username, algorithm)
    seen_count = recommender.count_seen(username)[0]['seen_count']
    rated_count = recommender.count_rated(username)[0]['rated_count']
    top_genres = recommender.top_genres(username)
    top_artists = recommender.top_artists(username)
    top_album = recommender.top_album(username)
    return render_template('recommendations.html', seen_count=seen_count, rated_count=rated_count, recommended_albums=recommended_albums, username=username, algorithm=algorithm, top_genres=top_genres, top_artists=top_artists, top_album=top_album)


@app.post('/recommend/<username>/<algorithm>')
def post_recommend(username, algorithm):
    # save the rated items, then recommend again
    # seen items are saved in get_recommend, since that's where they're shown
    for album_id in request.form.keys():
        rating = int(request.form.get(album_id, 0))
        print(f'User {username} rated album {album_id} with {rating}')
        if rating > 0: # it was rated, so we save it in the ratings table
            recommender.insert_rating(username, album_id, rating)

    return redirect(f'/recommend/{username}/{algorithm}')
