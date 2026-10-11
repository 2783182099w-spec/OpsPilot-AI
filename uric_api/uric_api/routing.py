from channels.routing import ProtocolTypeRouter

# WebSocket 请求交给 host 应用的 ws_urls 路由处理
from host import ws_urls

application = ProtocolTypeRouter({
    "websocket": ws_urls.ws_router,
})
