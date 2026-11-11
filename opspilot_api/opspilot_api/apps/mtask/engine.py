"""单执行器租约与数据库调度，不依赖 Redis/Celery。"""
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import APIException

from host.permissions import require_hosts
from .models import ExecutionTask, HostResult, TaskSchedule, WorkerLease


LEASE_SECONDS = 30


def acquire_lease():
    now = timezone.now()
    WorkerLease.objects.get_or_create(name='default', defaults={'expires_at': now, 'heartbeat_at': now})
    token = uuid.uuid4().hex
    changed = WorkerLease.objects.filter(name='default', expires_at__lte=now).update(token=token, heartbeat_at=now, expires_at=now + timedelta(seconds=LEASE_SECONDS))
    if not changed:
        return None
    # 只有取得过期租约后才恢复旧执行器遗留的任务，不抢活跃执行器任务。
    stale = ExecutionTask.objects.filter(status='running').exclude(worker_token=token)
    for task in stale:
        if task.options.get('managed'):
            task.host_results.filter(status='queued').update(status='skipped',may_have_run=False,
                failure_code='not_dispatched',output='执行器租约失效，尚未派发',finished_at=now)
            task.host_results.filter(status='running').update(status='interrupted',may_have_run=None,
                failure_code='remote_unknown',output='执行器租约失效，远端结果未知；禁止自动重试',finished_at=now)
        else:
            task.host_results.filter(status__in=['queued','running']).update(status='interrupted',
                output='执行器租约已失效，结果不确定；禁止自动重试',finished_at=now)
        ExecutionTask.objects.filter(pk=task.pk).update(status='interrupted', finished_at=now, completed_hosts=task.host_results.count())
        incident = task.options.get('incident',{})
        if incident.get('action') in ('ssh_password','ssh_root') and incident.get('escalate_on_failure'):
            from .diagnostics import escalate_repair
            escalate_repair(task,'执行器租约失效，须核对主机端回滚和当前 SSH 状态。')
    return token


def heartbeat(token):
    now = timezone.now()
    return bool(WorkerLease.objects.filter(name='default', token=token).update(heartbeat_at=now, expires_at=now + timedelta(seconds=LEASE_SECONDS)))


def release_lease(token):
    WorkerLease.objects.filter(name='default', token=token).update(expires_at=timezone.now())


def claim_task(token):
    if not heartbeat(token):
        return None
    task = ExecutionTask.objects.select_related('owner').filter(status='pending', cancel_requested=False).order_by('id').first()
    if task and ExecutionTask.objects.filter(pk=task.pk, status='pending', cancel_requested=False).update(status='running', worker_token=token, started_at=task.started_at or timezone.now()):
        task.worker_token = token
        return task
    return None


def dispatch_schedules():
    now = timezone.now()
    for schedule in TaskSchedule.objects.select_related('owner', 'last_task').filter(enabled=True, next_run__lte=now).order_by('next_run')[:10]:
        if not TaskSchedule.objects.filter(pk=schedule.pk, enabled=True, next_run=schedule.next_run).update(next_run=now + timedelta(minutes=schedule.interval_minutes)):
            continue
        if schedule.last_task and schedule.last_task.status in ('pending', 'running', 'awaiting_approval'):
            continue
        if ExecutionTask.objects.filter(status__in=['pending', 'running']).count() >= 100:
            TaskSchedule.objects.filter(pk=schedule.pk).update(last_error='队列已满，本轮跳过')
            continue
        try:
            require_hosts(schedule.owner, schedule.host_ids)
            if ExecutionTask.objects.filter(owner=schedule.owner, status__in=['pending', 'running']).count() >= 5:
                continue
            task = ExecutionTask.objects.create(owner=schedule.owner, host_ids=schedule.host_ids, command='', kind=schedule.kind, options={**schedule.options, 'schedule_id': schedule.id, 'schedule_name': schedule.name})
            TaskSchedule.objects.filter(pk=schedule.pk).update(last_task=task, last_error='')
        except APIException:
            TaskSchedule.objects.filter(pk=schedule.pk).update(enabled=False, last_error='资产授权已失效，计划已停用')
