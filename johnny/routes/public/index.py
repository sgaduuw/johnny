from flask import jsonify

from johnny.routes.public import public
from johnny.models import Ansible


@public.route('/')
def index():
    q_ansible = Ansible.find_all().to_list()
    l_ansible = [dict(a) for a in q_ansible]

    # remove id from result, it is not JSON serializable
    for a in l_ansible:
        del a['id']

    return jsonify(l_ansible)
