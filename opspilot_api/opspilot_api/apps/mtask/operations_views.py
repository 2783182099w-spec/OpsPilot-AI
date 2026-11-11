from datetime import timedelta
from django.conf import settings
from django.utils.dateparse import parse_datetime

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from host.permissions import require_hosts
from .models import TaskSchedule, CmdTemplate, TemplateRevision
from .serializers import CmdTemplateSerializer
from .inspection import validate_options
from .template_tools import validate_parameters, render


def first_run(value, interval):
    if not value:
        return timezone.now() + timedelta(minutes=interval)
    try:
        parsed = parse_datetime(str(value))
    except ValueError:
        parsed = None
    if not parsed or timezone.is_naive(parsed):
        raise ValidationError('首次执行时间须为带时区的未来时间')
    # Existing project uses naive local datetimes; preserve existing schedules
    # rather than reinterpret all historical database timestamps.
    if not settings.USE_TZ:
        parsed = parsed.astimezone().replace(tzinfo=None)
    if parsed <= timezone.now():
        raise ValidationError('首次执行时间须晚于当前时间')
    return parsed


def schedule_data(row):
    return {'id': row.id, 'name': row.name, 'host_ids': row.host_ids, 'kind': row.kind, 'options': row.options, 'interval_minutes': row.interval_minutes, 'enabled': row.enabled, 'next_run': row.next_run, 'last_task_id': row.last_task_id, 'last_error': row.last_error, 'last_status': row.last_task.status if row.last_task_id else None}


class SchedulesView(APIView):
    def visible(self, user):
        return TaskSchedule.objects.select_related('last_task').all() if user.is_superuser else TaskSchedule.objects.select_related('last_task').filter(owner=user)

    def get(self, request):
        return Response([schedule_data(row) for row in self.visible(request.user).order_by('-id')[:100]])

    def create(self, request):
        require_hosts(request.user, request.data.get('host_ids'))
        if self.visible(request.user).count() >= 50:
            raise ValidationError('最多配置 50 个计划')
        name = request.data.get('name')
        kind = request.data.get('kind', 'inspection')
        interval = request.data.get('interval_minutes', 60)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValidationError('计划名称须为 1 到 100 字')
        if type(interval) is not int or not 15 <= interval <= 10080:
            raise ValidationError('执行间隔为 15 分钟到 7 天')
        if kind not in ('probe', 'inspection'):
            raise ValidationError('定时计划仅支持端口检测和固定只读巡检')
        options = validate_options(kind, request.data.get('options', {}))
        next_run = first_run(request.data.get('next_run'), interval)
        row = TaskSchedule.objects.create(owner=request.user, name=name.strip(), host_ids=list(dict.fromkeys(request.data['host_ids'])), kind=kind, options=options, interval_minutes=interval, next_run=next_run)
        return Response(schedule_data(row), status=201)

    def patch(self, request, pk):
        with transaction.atomic():
            row = get_object_or_404(self.visible(request.user).select_for_update(), pk=pk)
            data = request.data
            name = data.get('name', row.name)
            interval = data.get('interval_minutes', row.interval_minutes)
            enabled = data.get('enabled', row.enabled)
            kind = data.get('kind', row.kind)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
                raise ValidationError('计划名称须为 1 到 100 字')
            if type(interval) is not int or not 15 <= interval <= 10080 or type(enabled) is not bool:
                raise ValidationError('间隔为 15 到 10080 分钟，enabled 必须为布尔值')
            if kind not in ('probe', 'inspection'):
                raise ValidationError('仅支持端口检测或只读巡检')
            ids = data.get('host_ids', row.host_ids)
            require_hosts(row.owner, ids)
            options = validate_options(kind, data.get('options', row.options))
            next_run = row.next_run if set(data) == {'enabled'} and enabled and row.next_run > timezone.now() else first_run(data.get('next_run'), interval)
            row.name, row.interval_minutes, row.enabled = name.strip(), interval, enabled
            row.kind, row.host_ids, row.options = kind, list(dict.fromkeys(ids)), options
            row.next_run, row.last_error = next_run, ''
            row.save()
        return Response(schedule_data(row))

    def post(self, request, pk=None):
        if pk is None:
            return self.create(request)
        from .task_views import submit, serialize
        with transaction.atomic():
            row = get_object_or_404(self.visible(request.user).select_for_update(), pk=pk)
            if request.data.get('confirmed') is not True:
                raise ValidationError('请确认立即执行')
            if row.last_task and row.last_task.status in ('pending', 'running', 'awaiting_approval'):
                raise ValidationError('该计划上次任务尚未结束')
            task = submit(row.owner, {'kind': row.kind, 'host_ids': row.host_ids, 'options': row.options, 'confirmed': True})
            task.options = {**task.options, 'schedule_id': row.id, 'schedule_name': row.name}
            task.save(update_fields=['options'])
            row.last_task, row.last_error = task, ''
            row.save(update_fields=['last_task', 'last_error'])
        return Response(serialize(task), status=202)

    def delete(self, request, pk):
        get_object_or_404(self.visible(request.user), pk=pk).delete()
        return Response(status=204)


class TemplateDetailView(APIView):
    def get(self, request, pk):
        template = get_object_or_404(CmdTemplate, pk=pk)
        return Response({'template': CmdTemplateSerializer(template).data, 'revisions': list(template.revisions.order_by('-version').values('version','command','parameters','description','created_at')[:30])})

    def patch(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied('模板修改限管理员')
        with transaction.atomic():
            template = get_object_or_404(CmdTemplate.objects.select_for_update(), pk=pk)
            if request.data.get('expected_version') != template.version:
                raise ValidationError('模板已被更新，请刷新后再编辑')
            serializer = CmdTemplateSerializer(template, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            TemplateRevision.objects.get_or_create(template=template, version=template.version, defaults={'command': template.cmd, 'parameters': template.parameters, 'description': template.description or ''})
            serializer.save(version=template.version+1)
        return Response(serializer.data)

    def post(self, request, pk):
        template = get_object_or_404(CmdTemplate, pk=pk, is_deleted=False)
        return Response({'command': render(template, request.data.get('parameters', {})), 'version': template.version})
