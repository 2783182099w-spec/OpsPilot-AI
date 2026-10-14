from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import DeepSeekError, ask_deepseek
from opspilot_api.utils.redaction import redact
from rest_framework.throttling import UserRateThrottle


class AIThrottle(UserRateThrottle):
    rate = "10/minute"


def _clean_history(raw_history):
    """只保留合法的 user / assistant 历史消息。"""
    history = []
    for item in raw_history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": str(content)})
    return history


class ChatView(APIView):
    """AI 对话：接收用户消息，转发到 DeepSeek 并返回回复。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AIThrottle]

    def post(self, request):
        raw_message = request.data.get("message")
        raw_history = request.data.get("history", [])
        if not isinstance(raw_message, str) or not isinstance(raw_history, list):
            return Response({"error": "message 必须是文本，history 必须是数组"}, status=400)
        message = raw_message.strip()
        if not message:
            return Response({"error": "请输入内容"}, status=status.HTTP_400_BAD_REQUEST)

        if len(message) > 20000 or len(raw_history) > 20:
            return Response({"error": "内容或历史记录过长"}, status=400)
        history = _clean_history(raw_history)
        if sum(len(item["content"]) for item in history) > 40000:
            return Response({"error": "上下文过长"}, status=400)
        message = redact(message)
        history = [{**item, "content": redact(item["content"])} for item in history]

        try:
            mode = request.data.get("mode", "chat")
            if mode not in ("chat", "logs", "diagnosis"):
                return Response({"error": "不支持的分析模式"}, status=400)
            reply = ask_deepseek(message, history=history, mode=mode, owner=request.user)
        except DeepSeekError as exc:
            from django.conf import settings
            from .models import AICall
            AICall.objects.create(owner=request.user, model=getattr(settings, "DEEPSEEK_MODEL", ""), mode=mode)
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"reply": reply})


class AIHistoryView(APIView):
    def get(self, request):
        from .models import AICall
        return Response(list(AICall.objects.filter(owner=request.user).order_by('-id').values('id', 'model', 'mode', 'input_tokens', 'output_tokens', 'success', 'created_at')[:50]))
