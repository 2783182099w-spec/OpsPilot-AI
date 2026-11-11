import math
from datetime import timedelta
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from host.permissions import accessible_hosts
from host.models import Host, Finding, AssetChange
from mtask.models import WorkerLease, ExecutionTask
from .models import MonitorTarget, MonitorRule, MonitorHealth
from .prometheus import latest, history, METRICS, Unavailable
from .configuration import validate_endpoint


def admin(user):
    if not user.is_superuser:
        raise PermissionDenied('监控接入与规则配置仅限管理员')


def target_data(row):
    return {'id':row.id,'host_id':row.host_id,'endpoint':row.endpoint,'enabled':row.enabled}


class OverviewView(APIView):
    def get(self, request):
        hosts = list(accessible_hosts(request.user).order_by('id')[:300])
        targets = {t.host_id:t for t in MonitorTarget.objects.filter(host__in=hosts)}
        ids = [i for i,t in targets.items() if t.enabled]
        error = ''
        try:
            data = latest(ids)
        except Unavailable as exc:
            data, error = {}, str(exc)
        now = timezone.now().timestamp()
        rows = []
        for host in hosts:
            sample, target = data.get(host.id, {}), targets.get(host.id)
            state = 'not_configured' if not target else 'disabled' if not target.enabled else 'unknown'
            if target and target.enabled and not error:
                stamp = sample.get('sample_time')
                state = 'up' if sample.get('up') == 1 else 'down' if sample.get('up') == 0 else 'unknown'
                if state=='up' and (sample.get('collector_failures') or 0)>0:
                    state='partial'
                if stamp is None or now-stamp > 180:
                    state = 'stale'
            rows.append({'id':host.id,'name':host.name,'ip_addr':host.ip_addr,'business':host.business,'environment':host.environment,'state':state,'target':target_data(target) if target else None,'metrics':sample if state in ('up','partial') else {}})
        return Response({'rows':rows,'error':error,'limit':300,'scrape_interval':60})


class TargetView(APIView):
    def patch(self, request, pk):
        admin(request.user)
        host = get_object_or_404(accessible_hosts(request.user),pk=pk)
        old = MonitorTarget.objects.filter(host=host).first()
        endpoint = validate_endpoint(request.data.get('endpoint', old.endpoint if old else ''))
        enabled = request.data.get('enabled',True)
        if type(enabled) is not bool:
            raise ValidationError('enabled 必须为布尔值')
        row, _ = MonitorTarget.objects.update_or_create(host=host,defaults={'owner':request.user,'endpoint':endpoint,'enabled':enabled})
        AssetChange.objects.create(host=host,actor=request.user,changes={'monitoring':{'endpoint':endpoint,'enabled':enabled}})
        return Response({**target_data(row),'message':'已保存，监控同步器将在约 30 秒内应用'})


class HistoryView(APIView):
    def get(self, request, pk):
        get_object_or_404(accessible_hosts(request.user),pk=pk)
        get_object_or_404(MonitorTarget,host_id=pk,enabled=True)
        metric = request.query_params.get('metric','cpu')
        try:
            hours = int(request.query_params.get('hours',6))
        except ValueError:
            raise ValidationError('时间范围无效')
        if metric not in METRICS or not 1 <= hours <= 168:
            raise ValidationError('指标或时间范围无效，最长 7 天')
        try:
            return Response({'metric':metric,'points':history(pk,metric,hours)})
        except Unavailable as exc:
            return Response({'detail':str(exc)},status=503)


class RulesView(APIView):
    def get(self, request):
        return Response(list(MonitorRule.objects.order_by('id').values()))

    def post(self, request, pk=None):
        admin(request.user)
        row = get_object_or_404(MonitorRule,pk=pk) if pk else MonitorRule()
        name = request.data.get('name', row.name)
        metric = request.data.get('metric',row.metric)
        severity = request.data.get('severity',row.severity)
        threshold = request.data.get('threshold',row.threshold)
        duration = request.data.get('duration',row.duration)
        recovery = request.data.get('recovery_seconds',row.recovery_seconds)
        enabled = request.data.get('enabled',row.enabled)
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 100 or metric not in ('up','cpu','memory','disk','inode','load','collector_failures') or severity not in ('warning','critical','info') or type(enabled) is not bool:
            raise ValidationError('名称、指标、级别或启用状态无效')
        if type(threshold) not in (int,float) or not math.isfinite(threshold) or not 0 <= threshold <= (10000 if metric in ('load','collector_failures') else 100):
            raise ValidationError('阈值范围无效')
        if type(duration) is not int or not 0 <= duration <= 86400 or type(recovery) is not int or not 0 <= recovery <= 3600:
            raise ValidationError('持续时间须为 0～86400 秒，恢复保持 0～3600 秒')
        if not pk and MonitorRule.objects.count() >= 50:
            raise ValidationError('最多配置 50 条规则')
        row.name,row.metric,row.severity = name.strip(),metric,severity
        row.threshold,row.duration,row.recovery_seconds,row.enabled = 1 if metric=='up' else threshold,duration,recovery,enabled
        row.save()
        return Response({'id':row.id,'message':'规则已保存，同步器将在约 30 秒内应用'},status=200 if pk else 201)

    patch = post


class PlatformHealthView(APIView):
    def get(self, request):
        now = timezone.now()
        lease = WorkerLease.objects.filter(name='default').first()
        monitor = MonitorHealth.objects.filter(name='default').first()
        tasks = ExecutionTask.objects.all() if request.user.is_superuser else ExecutionTask.objects.filter(owner=request.user)
        pending = tasks.filter(status='pending').order_by('created_at').first()
        return Response({'worker_active':bool(lease and lease.expires_at>now),'queue_pending':tasks.filter(status='pending').count(),'oldest_pending_at':pending.created_at if pending else None,'monitor_active':bool(monitor and monitor.heartbeat_at and monitor.heartbeat_at>now-timedelta(seconds=90)),'monitor_last_success':monitor.last_success_at if monitor else None,'monitor_error':monitor.error if monitor else '监控同步器未启动','config_error':monitor.config_error if monitor else '', 'retention_days':7,'notifications_enabled':False})


class WorkbenchView(APIView):
    def get(self, request, pk):
        from host.serializers import HostModelSerializer
        from host.operations import FindingsView
        from mtask.models import HostResult
        host = get_object_or_404(accessible_hosts(request.user),pk=pk)
        results = HostResult.objects.filter(host_id_snapshot=pk).select_related('task','task__owner')
        if not request.user.is_superuser:
            results = results.filter(task__owner=request.user)
        findings = FindingsView().visible(request.user).filter(host=host)
        target = MonitorTarget.objects.filter(host=host).first()
        return Response({'host':HostModelSerializer(host).data,'target':target_data(target) if target else None,'alerts':list(findings.order_by('-last_seen').values('id','title','condition','state','severity')[:20]),'tasks':[{'id':r.task_id,'kind':r.task.kind,'status':r.status,'created_at':r.task.created_at,'command':r.task.command[:500]} for r in results.order_by('-task_id')[:20]],'changes':list(host.changes.order_by('-id').values('created_at','actor__username','changes')[:20])})
