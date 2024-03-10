from bunnet import Document, Indexed


class Group(Document):
    name: Indexed(str, unique=True)
