from flask import render_template

from johnny.routes.public import public
from johnny.models import Ansible


@public.route('/list/')
def list_hosts():
    q_ansible = Ansible.find_all().to_list()
    l_ansible = [dict(a) for a in q_ansible]

    # remove id from result, it is not JSON serializable
    for a in l_ansible:
        del a['id']

    context = {
        'hostlist': l_ansible
    }

    return render_template('list.j2', **context)
