from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired


class LoginForm(FlaskForm):
    """Login form."""
    user_name = StringField("Username", validators=[DataRequired()])
    pass_word = PasswordField("Password", validators=[DataRequired()])
