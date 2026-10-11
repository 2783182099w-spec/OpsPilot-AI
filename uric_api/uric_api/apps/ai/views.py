from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import DeepSeekError, ask_deepseek


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

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"error": "请输入内容"}, status=status.HTTP_400_BAD_REQUEST)

        history = _clean_history(request.data.get("history"))

        try:
            reply = ask_deepseek(message, history=history)
        except DeepSeekError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"reply": reply})
