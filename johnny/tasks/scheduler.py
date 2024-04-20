from celery.schedules import crontab

from johnny.tasks import clry
from johnny.tasks import update_from_redis


@clry.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):

    sender.add_periodic_task(
        crontab(minute='*/5'),
        update_from_redis.s()
    )
