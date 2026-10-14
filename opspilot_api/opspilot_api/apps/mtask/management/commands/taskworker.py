import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from mtask.models import ExecutionTask
from mtask.tasks import run_task


class Command(BaseCommand):
    help = "单实例任务执行器；每个任务最多并发执行 4 台主机"

    def handle(self, *args, **options):
        # 本版本只支持一个 worker。中断中的任务标记失败，避免重复执行命令。
        ExecutionTask.objects.filter(status="running").update(status="failed", finished_at=timezone.now(), results=[{"error": "执行器重启，执行结果不确定；请人工核查，不自动重试"}])
        while True:
            close_old_connections()
            task = ExecutionTask.objects.select_related("owner").filter(status="pending").order_by("id").first()
            if task and ExecutionTask.objects.filter(pk=task.pk, status="pending").update(status="running"):
                run_task(task)
            else:
                time.sleep(1)
