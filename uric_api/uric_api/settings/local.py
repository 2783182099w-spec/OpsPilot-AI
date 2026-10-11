"""本地开发配置。

在 dev 配置的基础上，把数据库换成 SQLite、Channel 层换成进程内内存实现，
这样在没有 MySQL / Redis 的机器上也能直接启动项目。

用法（Windows PowerShell）:
    $env:DJANGO_SETTINGS_MODULE = "uric_api.settings.local"
    python manage.py runserver
"""

import os

from .dev import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# 本机没有 MySQL，改用 SQLite，数据文件为 uric_api/db_local.sqlite3
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_local.sqlite3",
    }
}

# 本机没有 Redis，WebSocket 使用进程内内存通道层
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# 本地开发放开跨域，方便前后端分端口联调
CORS_ORIGIN_ALLOW_ALL = True
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = False

# 本地开发默认没有可连的 SSH 主机，关闭新建主机时的 SSH 连通性校验。
# 想恢复真实校验（填错账号密码就会报错），把这里改成 True 即可。
HOST_SSH_VALIDATE = False

# DeepSeek 密钥不入库，通过环境变量注入：
#   PowerShell : $env:DEEPSEEK_API_KEY = "sk-xxxx"
#   Linux/macOS: export DEEPSEEK_API_KEY="sk-xxxx"
# 也可以写在同目录的 local_secret.py 里（该文件已被 .gitignore 忽略）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# 本地私有配置（数据库口令、密钥等），存在则加载、不存在则忽略
try:
    from .local_secret import *  # noqa: F401,F403
except ImportError:
    pass
