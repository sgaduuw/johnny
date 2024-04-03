from bunnet import init_bunnet
from environs import Env
from flask import Flask
from pymongo import MongoClient

from johnny.config import FlaskConfig
from johnny.extensions import bootstrap, csrf, debug_toolbar, login_manager
from johnny.routes.public import public

env = Env()
c_mongo = MongoClient(env.str("MONGODB_URL"))


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(FlaskConfig)
    register_extensions(app)
    register_blueprints(app)

    init_bunnet(
        database=c_mongo.johnny,
        document_models=[
            'johnny.models.Group',
            'johnny.models.Ansible',
            'johnny.models.User'
        ],
        recreate_views=True
    )
    return app


def register_extensions(app: Flask) -> None:
    bootstrap.init_app(app)
    csrf.init_app(app)
    debug_toolbar.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    return None

def register_blueprints(app: Flask) -> None:
    """ Register Flask blueprints. """
    app.register_blueprint(public)
    return None


app = create_app()
