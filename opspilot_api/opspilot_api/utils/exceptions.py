import logging

from django.db import DatabaseError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("django")


def custom_exception_handler(exc, context):
    """自定义异常处理：DRF 能识别的异常沿用默认响应，其余记录日志并返回统一格式。"""
    response = exception_handler(exc, context)

    if response is None:
        view = context.get("view")
        logger.exception("[%s] 未处理异常", view)

        # 调试模式下交给 Django 输出完整堆栈，便于排查
        from django.conf import settings
        if settings.DEBUG:
            return None

        if isinstance(exc, DatabaseError):
            return Response(
                {"error": "服务器内部存储错误"},
                status=status.HTTP_507_INSUFFICIENT_STORAGE,
            )

        return Response(
            {"error": "服务器内部错误"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response
