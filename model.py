#!/username/bin/python

import os
from datetime import datetime
from peewee import Model, SqliteDatabase
from peewee import BooleanField, ForeignKeyField, CharField, DateTimeField, FloatField

PATH = os.path.dirname(os.path.abspath(__file__))

# db = SqliteDatabase(':memory:')
db = SqliteDatabase(PATH+'/airtel.sqlite', threadlocals=True)
# db = MySQLDatabase(
#     '<DATABASE>', host='<HOST>', port=3306, user='<USER>', password='<>')


class BaseModel(Model):
    class Meta:
        database = db


class Users(BaseModel):
    username = CharField(unique=True, index=True)
    password = CharField()
    name = CharField()
    created = DateTimeField(default=datetime.now)
    updated = DateTimeField(default=datetime(1970, 1, 1, 0, 0, 0, 0))
    balance = FloatField(default=0)
    old_balance = FloatField(default=0)
    threshold = FloatField(default=100)
    validity = DateTimeField(default=datetime(1970, 1, 1, 0, 0, 0, 0))
    active = BooleanField(default=True)


class Notifications(BaseModel):
    user = ForeignKeyField(Users, on_delete='CASCADE', to_field=Users.username)
    n_id = CharField(index=True)
    n_type = CharField()
    created = DateTimeField(default=datetime.now)
    updated = DateTimeField(default=datetime(1970, 1, 1, 0, 0, 0, 0))
    active = BooleanField(default=True)


def init():
    db.create_tables([Users, Notifications], safe=True)


def drop():
    db.drop_tables([Users, Notifications], safe=True)


def reset_all():
    Users.delete().execute()
    Notifications.delete().execute()

if __name__ == "__main__":
    pass
