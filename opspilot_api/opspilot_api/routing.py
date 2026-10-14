import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'opspilot_api.settings.local')
from django.core.asgi import get_asgi_application
http_application = get_asgi_application()
from django.conf import settings
if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
    http_application = ASGIStaticFilesHandler(http_application)
from channels.routing import ProtocolTypeRouter
from channels.security.websocket import AllowedHostsOriginValidator

# WebSocket 请求交给 host 应用的 ws_urls 路由处理
from host import ws_urls

application = ProtocolTypeRouter({
    "http": http_application,
    "websocket": AllowedHostsOriginValidator(ws_urls.ws_router),
})
