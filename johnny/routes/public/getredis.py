import json

import redis
from environs import Env
from flask import jsonify

from johnny.models import Ansible
from johnny.routes.public import public

env = Env()

c_redis = redis.Redis(
        host=env.str("REDIS_HOST"),
        port=env.int("REDIS_PORT"),
        protocol=env.int("REDIS_PROTOCOL"),
        username=env.str("REDIS_USERNAME"),
        password=env.str("REDIS_PASSWORD"),
        db=env.int("REDIS_DB"),
        ssl=env.bool("REDIS_SSL"),
        decode_responses=env.bool("REDIS_DECODE_RESPONSES")
    )

@public.route('/getredis/')
def getredis():

    try:
        c_redis.ping()

    except ConnectionError as e:
        print(e)

    else:
        for key in c_redis.keys('ansible_facts*'):

            data = json.loads(c_redis.get(key))

            if data is not None:
                fqdn = data['ansible_fqdn']

                q_ansible = ~Ansible.find_one(Ansible.fqdn == fqdn)

                if q_ansible is None:
                    Ansible(
                        fqdn=fqdn,
                        data=data
                    ).insert()
                else:
                    q_ansible.data = data
                    q_ansible.save()


    finally:
        c_redis.close()

    context = {
        'r_keys': c_redis.keys()
    }

    return jsonify(context)
