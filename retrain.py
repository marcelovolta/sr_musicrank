import datetime
import os
import threading
import time
import traceback

import dataset
import ensemble
import models
import recommender


# Retraining from the app: the final model of one algorithm trained again with
# the current reviews (RYM plus this app's ratings), keeping the hyperparameters
# Optuna chose. It doesn't run Optuna again (that's pipeline.py, much slower).
# The training runs in a background thread, one at a time; the page asks for
# its state until it's done.
RETRAIN_DB = os.path.join(dataset.ROOT, 'data', 'work', 'retrain', 'mergenres.db')


def models_for(algorithm):
    # the trained models behind an algorithm: the model itself, the base models
    # of the ensemble (and of the average, which uses the same ones), and the
    # model of a cascade. The SQL algorithms have none
    name = algorithm.removeprefix('cascade_')
    if name in models.NAMES:
        return [name]
    if name in ('ensemble', 'average'):
        return list(ensemble.BASE_MODELS)
    return []


def retrain(name, save=True):
    # The model trained again like the final one of the pipeline: every user
    # with at least dataset.MIN_USER_FINAL reviews, albums filtered with the
    # model's min_album, its hyperparameters. The new .pkl replaces the old one
    # only once it's complete (models.save writes a temporary file and renames
    # it), so the app keeps recommending with the old one meanwhile. The
    # optimization record stays (the NDCG was measured with those
    # hyperparameters), with the new number of reviews and the date.
    old = models.model(name)
    # a consistent copy of the app's database (the app keeps writing to it)
    database = dataset.copy_database(source=recommender.DATABASE_FILE, target=RETRAIN_DB)
    df = dataset.read_reviews(database)
    filtered = dataset.filter_albums(dataset.universe(df, dataset.MIN_USER_FINAL), old['params']['min_album'])
    m = models.train(name, filtered, old['params'], database)
    m['optimization'] = {**old['optimization'], 'reviews': len(filtered),
                         'retrained': datetime.datetime.now().isoformat(timespec='minutes')}
    if save:
        models.save(m)
    return m


_lock = threading.Lock()
_job = {'state': 'idle'}  # the last (or current) retraining


def status():
    # copy of the current job's state: idle, running, done or error
    with _lock:
        return dict(_job)


def start(algorithm):
    # starts retraining the algorithm's models in the background. Returns the
    # job's state; if another retraining is running it isn't started (training
    # two models at once would make both slower and compete for memory)
    names = models_for(algorithm)
    with _lock:
        if _job['state'] == 'running':
            return dict(_job)
        if not names:
            return {'state': 'error', 'error': f'{algorithm} has no trained model'}
        _job.clear()
        _job.update(state='running', algorithm=algorithm, models=names, done=[], current=names[0],
                    started=time.time())
        threading.Thread(target=_run, args=(names,), daemon=True).start()
        return dict(_job)


def _run(names):
    # the background thread: the models one after the other
    try:
        for name in names:
            with _lock:
                _job['current'] = name
            retrain(name)
            with _lock:
                _job['done'].append(name)
        with _lock:
            _job.update(state='done', current=None, seconds=round(time.time() - _job['started']))
    except Exception as e:
        traceback.print_exc()
        with _lock:
            _job.update(state='error', error=f'{type(e).__name__}: {e}')
