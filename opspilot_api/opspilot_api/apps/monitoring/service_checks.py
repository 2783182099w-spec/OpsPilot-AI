from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import http.client
from ipaddress import ip_address

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Finding, FindingNote
from host.permissions import accessible_hosts
from mtask.diagnostics import escalate_repair, queue_diagnosis, queue_repair, recommended_tree, safe_path
from mtask.managed import UNIT, bounded_int, clean_text
from mtask.models import DiagnosticTree
from .models import ServiceCheck

LIMIT = 32
MODES = ('monitor','diagnose','approval','auto')
SEVERITY = ('info','warning','critical')


def probe_address(host):
    try:
        address = ip_address(host.ip_addr)
        if (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast
                or address.is_reserved or getattr(address, 'ipv4_mapped', None) or '%' in str(address)):
            raise ValueError
        return str(address)
    except ValueError as exc:
        raise ValidationError('HTTP 探测需要资产中登记的普通 IPv4/IPv6 地址') from exc


def http_sample(address, port, path):
    connection = http.client.HTTPConnection(address, port, timeout=2)
    try:
        connection.request('GET', path, headers={'Connection':'close','User-Agent':'OpsPilot-Service-Check'})
        with connection.getresponse() as response:
            return 200 <= response.status < 300, f'HTTP {response.status}'
    except (OSError, http.client.HTTPException) as exc:
        return False, f'探测失败：{type(exc).__name__}'
    finally:
        connection.close()


def probe_data(row):
    fresh = row.enabled and row.last_checked_at and row.last_checked_at >= timezone.now()-timedelta(seconds=120)
    return {'id':row.pk,'host_id':row.host_id,'host_name':row.host.name,'name':row.name,'unit':row.unit,
            'path':row.path,'port':row.port,'mode':row.mode,'severity':row.severity,'tree_id':row.tree_id,
            'enabled':row.enabled,'escalate_on_failure':row.escalate_on_failure,'version':row.version,'state':row.state if fresh else 'unknown','checked_at':row.last_checked_at,
            'evidence':row.last_evidence,'failures':row.failures,'successes':row.successes}


class ServiceChecksView(APIView):
    def get(self, request):
        from django.conf import settings
        rows = ServiceCheck.objects.filter(host__in=accessible_hosts(request.user)).select_related('host').order_by('host_id','id')
        return Response({'results':[probe_data(row) for row in rows[:LIMIT]],
                         'automatic_repair_enabled':getattr(settings,'OPSPILOT_AUTOMATIC_REPAIR',False)})

    @transaction.atomic
    def post(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可维护服务探测与自愈策略')
        body = request.data
        existing = get_object_or_404(ServiceCheck.objects.select_for_update(),pk=pk) if pk else None
        if existing and existing.version != body.get('version'):
            raise ValidationError('策略已变化，请刷新重试')
        host_id = body.get('host_id')
        if type(host_id) is not int or host_id < 1:
            raise ValidationError('请选择有效的资产')
        host = get_object_or_404(accessible_hosts(request.user),pk=host_id)
        if existing and existing.host_id != host.pk:
            raise ValidationError('已有探测绑定固定资产')
        probe_address(host)
        if not existing and ServiceCheck.objects.count() >= LIMIT:
            raise ValidationError(f'最多配置 {LIMIT} 个 HTTP 探测')
        unit, mode, severity, enabled = (body.get(k) for k in ('unit','mode','severity','enabled'))
        if (not isinstance(unit,str) or (unit and not UNIT.fullmatch(unit)) or mode not in MODES
                or severity not in SEVERITY or type(enabled) is not bool):
            raise ValidationError('探测模式、级别、服务单元或启用状态不合法')
        if mode in ('approval','auto') and (unit != 'nginx.service' or (mode == 'auto' and host.environment != 'test')):
            raise ValidationError('仅 nginx.service 支持修复，自动模式限测试资产')
        tree_id = body.get('tree_id')
        tree = None
        if tree_id is not None:
            if type(tree_id) is not int or tree_id < 1:
                raise ValidationError('诊断树 ID 无效')
            tree = get_object_or_404(DiagnosticTree,pk=tree_id)
        escalate = body.get('escalate_on_failure',True)
        if type(escalate) is not bool:
            raise ValidationError('失败转人工必须为布尔值')
        fields = {'escalate_on_failure':escalate,'host':host,'owner':request.user,'name':clean_text(body.get('name'),100,'探测名称',True),
                  'unit':unit,'mode':mode,'severity':severity,'enabled':enabled,'tree':tree,
                  'port':bounded_int(body.get('port'),1,65535,'HTTP 端口'),'path':safe_path(body.get('path'))}
        if ServiceCheck.objects.filter(host=host,port=fields['port'],path=fields['path']).exclude(
                pk=existing.pk if existing else None).exists():
            raise ValidationError('该主机和 HTTP 端点已有策略')
        if existing:
            for key, value in fields.items():
                setattr(existing,key,value)
            existing.version += 1
            existing.last_checked_at = None
            existing.state = 'unknown'
            existing.successes = existing.failures = 0
            existing.save()
            mark_unknown(existing.pk)
            row = existing
        else:
            row = ServiceCheck.objects.create(**fields)
        return Response(probe_data(row),status=200 if existing else 201)


def mark_unknown(probe_id):
    Finding.objects.filter(dedupe_key=f'service:{probe_id}',condition='active').update(condition='unknown')


def sync_service_checks():
    rows = list(ServiceCheck.objects.filter(enabled=True,host__is_deleted=False)
                .exclude(host__asset_status__in=['retired','maintenance'])
                .select_related('host')[:LIMIT])
    ServiceCheck.objects.exclude(pk__in=[row.pk for row in rows]).exclude(state='unknown').update(state='unknown')
    unavailable = ServiceCheck.objects.exclude(pk__in=[row.pk for row in rows]).values_list('pk',flat=True)
    for probe_id in unavailable:
        mark_unknown(probe_id)

    def sample(row):
        try:
            address = probe_address(row.host)
        except ValidationError:
            return None, '资产 IP 已变化，探测暂停'
        return http_sample(address,row.port,row.path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for row, (ok, evidence) in zip(rows, pool.map(sample, rows)):
            apply_sample(row.pk,row.version,row.host.ip_addr,ok,evidence)


@transaction.atomic
def apply_sample(pk, version, address, ok, evidence):
    probe = ServiceCheck.objects.select_for_update().select_related('host','owner').filter(
        pk=pk,version=version,enabled=True,host__ip_addr=address,host__is_deleted=False).exclude(
        host__asset_status__in=['retired','maintenance']).first()
    if probe is None:
        return
    now = timezone.now()
    if not probe.last_checked_at or probe.last_checked_at < now - timedelta(seconds=120):
        probe.successes = probe.failures = 0
    probe.last_checked_at, probe.last_evidence = now, evidence
    probe.successes = min(probe.successes + 1,3) if ok is True else 0
    probe.failures = min(probe.failures + 1,3) if ok is False else 0
    probe.state = ('healthy' if probe.successes >= 3 else 'down' if probe.failures >= 3 else
                   'unknown' if ok is None else 'degraded')
    probe.save(update_fields=['last_checked_at','last_evidence','successes','failures','state'])
    key = f'service:{probe.pk}'
    if probe.state == 'unknown':
        mark_unknown(probe.pk)
        return
    if probe.state == 'healthy':
        finding = Finding.objects.filter(dedupe_key=key).exclude(condition='recovered').first()
        if finding:
            finding.condition, finding.recovered_at = 'recovered', now
            finding.save(update_fields=['condition','recovered_at','last_seen'])
            FindingNote.objects.create(finding=finding,text='连续 3 次 HTTP 探测通过，确认恢复')
        return
    if probe.state != 'down':
        return
    finding, created = Finding.objects.get_or_create(dedupe_key=key,defaults={
        'host':probe.host,'owner':probe.owner,'title':probe.name,'source':'service','condition':'active',
    })
    if not created and finding.condition == 'recovered':
        finding.occurrences += 1
        finding.state = 'open'
        FindingNote.objects.create(finding=finding,text='HTTP 探测再次连续失败，开启新轮次')
    finding.condition, finding.recovered_at = 'active', None
    finding.title, finding.severity = probe.name, probe.severity
    finding.evidence = f'{evidence} · 已连续失败 {probe.failures} 次'
    finding.signal = {'check_id':probe.pk,'metric':'HTTP','value':evidence,'threshold':'HTTP 2xx',
                      'sample_time':now.isoformat(),'duration':60,'recovery_seconds':60}
    finding.save()
    previous = finding.diagnostic_runs.filter(mode='repair',occurrence=finding.occurrences,escalated_at__isnull=True,
                                              task__status__in=['completed','failed','interrupted']).select_related('task').last()
    if previous:
        task = previous.task
        if task.status in ('failed','interrupted'):
            escalate_repair(task,'自动动作失败或未能确认结果，请核对任务证据。')
        elif task.finished_at and task.finished_at < now-timedelta(seconds=120):
            escalate_repair(task,'动作已完成，但外部 HTTP 探测仍然异常。')
    dispatch_incident(finding,probe,now)


def dispatch_incident(finding, probe, now):
    from mtask.diagnostic_workflows import owns_alert
    if owns_alert(finding):
        return
    if (probe.mode == 'monitor' or not probe.owner.is_active or not probe.owner.is_superuser
            or (finding.silenced_until and finding.silenced_until > now)):
        return
    runs = finding.diagnostic_runs.filter(occurrence=finding.occurrences)
    try:
        if not runs.filter(mode='diagnosis').exists():
            queue_diagnosis(finding,recommended_tree(finding,probe),probe.owner,automatic=True)
        elif probe.mode == 'auto' and runs.filter(mode='diagnosis',task__status='completed').exists() and not runs.filter(mode='repair').exists():
            queue_repair(finding,probe,probe.owner,automatic=True)
    except (ValidationError, PermissionDenied):
        # Disabled policies and full queues are evaluated again at the next sample.
        return
