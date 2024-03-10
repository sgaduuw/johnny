from bunnet import Document, Indexed


class Ansible(Document):
    fqdn: Indexed(str, unique=True)
    data: dict
