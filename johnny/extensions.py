from flask_bootstrap import Bootstrap5
from flask_debugtoolbar import DebugToolbarExtension
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

bootstrap = Bootstrap5()
csrf = CSRFProtect()
debug_toolbar = DebugToolbarExtension()
login_manager = LoginManager()
