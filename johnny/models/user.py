from datetime import datetime

from bunnet import Document
from flask_login import UserMixin
from pydantic import EmailStr
from werkzeug.security import generate_password_hash, check_password_hash


class User(UserMixin, Document):
    email: EmailStr
    username: str
    password_hash: str
    created_at: datetime = datetime.now
    updated_at: datetime = datetime.now

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
