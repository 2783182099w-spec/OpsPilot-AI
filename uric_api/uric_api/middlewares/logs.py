import logging
import time

logger = logging.getLogger("django")


class LogMiddleWare:
    """记录耗时超过 0.5 秒的请求，方便定位慢接口。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.time()
        response = self.get_response(request)
        cost = time.time() - start

        if cost > 0.5:
            logger.warning("请求路径: %s 耗时 %.2f 秒", request.path, cost)

        return response
