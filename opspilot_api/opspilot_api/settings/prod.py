"""生产环境配置。

在 dev 配置基础上关闭调试、从环境变量读取敏感信息，并改用 Redis 作为
Channel 层（需要额外安装 channels_redis）。
"""

import os

from .dev import *  # noqa: F401,F403

DEBUG = False

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if host]

# 生产环境的跨域白名单，逗号分隔
CORS_ORIGIN_WHITELIST = tuple(
    origin for origin in os.environ.get("DJANGO_CORS_ORIGINS", "").split(",") if origin
)

# 多进程部署时 Channel 层必须使用 Redis，否则 WebSocket 无法跨进程通信
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                (
                    os.environ.get("REDIS_HOST", "127.0.0.1"),
                    int(os.environ.get("REDIS_PORT", "6379")),
                )
            ],
        },
    },
}

DATABASES["default"].update(HOST=os.environ.get("MYSQL_HOST", "db"), NAME=os.environ.get("MYSQL_DATABASE", "opspilot"))
DATABASES["default"]["PASSWORD"] = os.environ["MYSQL_PASSWORD"]
SSH_REQUIRE_ENCRYPTED_KEYS = True
