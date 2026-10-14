from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from host.permissions import require_hosts
from .models import ExecutionTask
from django.utils import timezone


def serialize(task):
    return {"id": task.id, "command": task.command, "host_ids": task.host_ids, "status": task.status, "total": len(task.host_ids), "done": len(task.results), "results": task.results, "created_at": task.created_at, "finished_at": task.finished_at}


class TasksView(APIView):
    def get(self, request, pk=None):
        queryset = ExecutionTask.objects.all() if request.user.is_superuser else ExecutionTask.objects.filter(owner=request.user)
        if pk is not None:
            return Response(serialize(get_object_or_404(queryset, pk=pk)))
        return Response([serialize(task) for task in queryset.order_by("-id")[:50]])

    def post(self, request, pk=None):
        if pk is not None:
            if not request.user.is_superuser:
                return Response({"error": "仅管理员可以审批"}, status=403)
            task = get_object_or_404(ExecutionTask, pk=pk, status="awaiting_approval")
            require_hosts(task.owner, task.host_ids)
            task.status = "pending"
            task.approved_by = request.user
            task.approved_at = timezone.now()
            task.save(update_fields=["status", "approved_by", "approved_at"])
            return Response(serialize(task))
        ids = request.data.get("host_ids")
        require_hosts(request.user, ids)
        cmd = request.data.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 10000:
            return Response({"error": "命令必须为 1 到 10000 字符"}, status=400)
        if request.data.get("confirmed") is not True:
            return Response({"error": "请确认目标主机与命令后执行"}, status=400)
        if ExecutionTask.objects.filter(owner=request.user, status__in=["pending", "running", "awaiting_approval"]).count() >= 5:
            return Response({"error": "同时最多提交 5 个任务"}, status=429)
        task = ExecutionTask.objects.create(owner=request.user, host_ids=list(dict.fromkeys(ids)), command=cmd, status="pending" if request.user.is_superuser else "awaiting_approval")
        return Response(serialize(task), status=202)
