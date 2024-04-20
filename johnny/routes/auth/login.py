from flask import request, render_template
from flask_login import login_user

from johnny.extensions import login_manager
from johnny.models import User
from johnny.routes.auth import auth_bp

@login_manager.user_loader
def load_user(user_id):

    return ~User.find_one(User.id == user_id)


@auth_bp.route('/login/', methods=['GET', 'POST'])
def login():

    match request.method:
        case 'POST':
            pass

        case 'GET':
            pass

        case _:
            pass

    context = {}

    return render_template('login.j2', **context)