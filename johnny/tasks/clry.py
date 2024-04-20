from johnny import app

from johnny.celery import create_celery

clry = create_celery(app)
