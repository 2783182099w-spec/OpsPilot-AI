import os
from .local import *

DEBUG = False
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/app/data/opspilot.sqlite3", "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"}}}
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_ALL_ORIGINS = False
CORS_ORIGIN_WHITELIST = ()
HOST_SSH_VALIDATE = False
SSH_REQUIRE_ENCRYPTED_KEYS = True
CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [("redis", 6379)]}}}
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": "INFO"}}
