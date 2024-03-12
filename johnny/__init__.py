from bunnet import init_bunnet
from environs import Env
from flask import Flask
from pymongo import MongoClient

from johnny.config import FlaskConfig
from johnny.routes.public import public

env = Env()
c_mongo = MongoClient(env.str("MONGODB_URL"))


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(FlaskConfig)
    register_blueprints(app)

    init_bunnet(
        database=c_mongo.johnny,
        document_models=[
            'johnny.models.Group',
            'johnny.models.Ansible'
        ],
        recreate_views=True
    )
    return app


def register_blueprints(app) -> None:
    """ Register Flask blueprints. """
    app.register_blueprint(public)
    return None


app = create_app()
