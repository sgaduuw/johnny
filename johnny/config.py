from environs import Env

env = Env()

class Celery:
    CELERY_CONFIG = {
        'broker_url': env.str('CELERY_BROKER_URL'),
        'broker_connection_retry': True,
        'broker_connection_retry_on_startup': True,
        'worker_cancel_long_running_tasks_on_connection_loss': True,
        'worker_prefetch_multiplier': 0
    }


class FlaskConfig(Celery):
    DEBUG = env.bool('FLASK_DEBUG')
