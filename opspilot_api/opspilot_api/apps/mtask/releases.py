from collections import Counter, defaultdict
from pathlib import Path
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Host
from host.permissions import require_hosts
from opspilot_api.utils.redaction import redact
from .managed import ACTIVE, UNIT, bounded_int, check_plan, clean_text, signed_plan, target_snapshot
from .models import ExecutionTask, HostResult, ReleaseApplication, ReleaseArtifact, ReleaseRun, WorkerLease
from .scripts import validate_script
from .task_views import enqueue, result_data, visible_tasks

SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
PATH = re.compile(r'/[A-Za-z0-9/_.~-]{0,150}')
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,79}')
DIGEST = re.compile(r'[0-9a-f]{64}')
RELEASE_STATUSES = (*ACTIVE, "completed", "failed", "interrupted", "cancelled", "rejected")
SCRIPT_PATH = Path(__file__).with_name('release_script.py')


def legacy_targets():
    config = getattr(settings,'OPSPILOT_RELEASE_TARGETS',{})
    return config if isinstance(config,dict) else {}


def release_target(application):
    if not isinstance(application,str) or not SLUG.fullmatch(application):
        raise ValidationError('发布应用尚未配置')
    row = ReleaseApplication.objects.filter(application=application).only('target','enabled').first()
    if row and not row.enabled:
        raise ValidationError('该发布应用已停用')
    config = row.target if row else legacy_targets().get(application)
    return normalize_target(config)


def admission_lock():
    now = timezone.now()
    WorkerLease.objects.select_for_update().get_or_create(
        name='release-admission',defaults={'expires_at':now,'heartbeat_at':now})


def normalize_target(config):
    if not isinstance(config,dict):
        raise ValidationError('发布应用尚未配置')
    unit = config.get('unit', '')
    hosts = config.get('host_ids')
    path = config.get('path', '/health')
    origin = config.get('origin', '')
    if (not isinstance(unit,str) or not UNIT.fullmatch(unit) or not isinstance(hosts, list) or not 1 <= len(hosts) <= 300
            or any(type(pk) is not int or pk < 1 for pk in hosts) or not isinstance(path,str) or not PATH.fullmatch(path) or '..' in path.split('/') or path.startswith('//')
            or config.get('environment') not in ('test', 'staging', 'production')
            or not isinstance(origin,str) or len(origin)>200 or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', origin)):
        raise ValidationError('发布应用配置无效')
    try:
        origin_port = urlsplit(origin).port
        if origin_port == 0:
            raise ValueError
    except ValueError as exc:
        raise ValidationError('制品来源的 HTTPS 端口无效') from exc
    return {'unit': unit, 'host_ids': sorted(set(hosts)), 'path': path,
            'port': bounded_int(config.get('port', 80), 1, 65535, '验证端口'),
            'environment': config['environment'], 'origin': origin}


def manifest(body, origin):
    if not hasattr(body,'get'):
        raise ValidationError('制品清单必须为对象')
    version, url, sha = (body.get(key, '') for key in ('version', 'url', 'sha256'))
    if not all(isinstance(item, str) for item in (version, url, sha)):
        raise ValidationError('制品版本、URL 和校验值必须是文本')
    try:
        parsed = urlsplit(url)
        if (not VERSION.fullmatch(version) or not DIGEST.fullmatch(sha)
                or len(url) > 220 or f'{parsed.scheme}://{parsed.netloc}' != origin
                or not re.fullmatch(r'/[A-Za-z0-9/_.~-]{1,180}', parsed.path)
                or any(part in ('..', '.') for part in parsed.path.split('/'))
                or url != origin + parsed.path or not 1 <= (parsed.port or 443) <= 65535):
            raise ValueError
    except ValueError as exc:
        raise ValidationError('需要合法版本、白名单 HTTPS 制品路径和小写 SHA-256；URL 不可带凭据、参数或片段') from exc
    return {'version': version, 'url': url, 'sha256': sha}


def release_data(row):
    task = row.task
    return {'id': row.id, 'application': row.application, 'version': row.artifact['version'],
            'sha256': row.artifact['sha256'], 'url': row.artifact['url'], 'host_ids': task.host_ids,
            'task_id': task.pk, 'status': task.status, 'actor': task.owner.username,
            'approved_by': task.approved_by.username if task.approved_by else None,
            'rollback_of': row.rollback_of_id, 'created_at': task.created_at,
            'released_by': task.released_by.username if task.released_by else None,
            'environment': task.options.get('release', {}).get('target', {}).get('environment'),
            'done': task.completed_hosts, 'total': len(task.host_ids),
            'started_at': task.started_at, 'finished_at': task.finished_at}


def release_rows(user):
    return ReleaseRun.objects.filter(task__in=visible_tasks(user)).select_related(
        'task__owner','task__approved_by','task__released_by',
    ).order_by('-id')


def rollback_scope(current, config):
    prior = current.task.options.get('release',{}).get('target',{})
    if current.task.status in ACTIVE or any(prior.get(key) != config[key] for key in ('unit','environment')):
        return set()
    allowed = set(Host.objects.filter(pk__in=config['host_ids'],environment=config['environment'],
                                     is_deleted=False).exclude(asset_status='retired').values_list('pk',flat=True))
    affected = set(current.task.host_results.exclude(may_have_run=False).values_list('host_id_snapshot',flat=True))
    later = ReleaseRun.objects.filter(application=current.application,id__gt=current.pk)
    in_flight = later.filter(task__status__in=ACTIVE).values_list('task__host_ids',flat=True)
    for ids in in_flight:
        affected.difference_update(ids)
    superseded = HostResult.objects.filter(task__release_run__in=later).exclude(may_have_run=False)
    affected.difference_update(superseded.values_list('host_id_snapshot',flat=True))
    return affected & allowed


def check_rollback(current, previous, ids, config):
    prior = previous.task.options.get('release',{}).get('target',{})
    if (previous.application != current.application or previous.pk >= current.pk or previous.task.status != 'completed'
            or any(prior.get(key) != config[key] for key in ('unit','environment'))):
        raise ValidationError('请选择更早的同应用成功发布')
    if not set(ids) <= rollback_scope(current,config):
        raise ValidationError('选中主机未在来源发布中执行、已下线，或存在后续发布')
    if previous.task.host_results.filter(status='success',host_id_snapshot__in=ids).count() != len(ids):
        raise ValidationError('回退版本必须在所有选中主机上验证成功过')


class ReleaseDetailView(APIView):
    def get(self, request, pk):
        row = get_object_or_404(release_rows(request.user),pk=pk)
        task = row.task
        policy = task.options.get('release',{}).get('target',{})
        managed = task.options.get('managed',{})
        admin = request.user.is_superuser
        independent = admin and request.user.pk != task.owner_id
        state = task.status
        results = task.host_results.order_by('id')
        summary = dict(Counter(results.values_list('status',flat=True)))
        rows = results
        selection = request.query_params.get('result')
        if selection == 'attention':
            rows = rows.filter(
                ~Q(status__in=['success','queued','running','skipped','cancelled']) |
                (Q(status='cancelled') & ~Q(may_have_run=False))
            )
        elif selection in ('success','queued','skipped','running'):
            rows = rows.filter(status=selection)
        pager = PageNumberPagination()
        pager.page_size = 15
        hosts = pager.get_paginated_response([result_data(result) for result in pager.paginate_queryset(rows,request)]).data
        return Response({
            **release_data(row),'summary':summary,'hosts':hosts,'reason':redact(managed.get('reason','')),
            'stop_reason':task.stop_reason,'approved_at':task.approved_at,'released_at':task.released_at,
            'rollout_phase':task.rollout_phase,'cancel_requested':task.cancel_requested,
            'restore_from':task.options.get('release',{}).get('restore_from'),
            'policy':{key:policy.get(key) for key in ('unit','port','path')},
            'actions':{
                'approve':independent and state=='awaiting_approval',
                'reject':independent and state=='awaiting_approval',
                'release':independent and state=='awaiting_release',
                'cancel':(admin or task.owner_id==request.user.pk) and state in ACTIVE and not task.cancel_requested,
                'rollback':admin and state not in ACTIVE and any(summary.get(code) for code in ('success','failed','interrupted','timeout','cancelled')),
            },
        })


class ReleaseRollbackView(APIView):
    def get(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可以准备回退')
        current = get_object_or_404(release_rows(request.user),pk=pk)
        config = release_target(current.application)
        eligible = rollback_scope(current,config)
        history = ReleaseRun.objects.filter(application=current.application,pk__lt=pk,task__status='completed',
                                            task__host_results__status='success',
                                            task__host_results__host_id_snapshot__in=eligible,
                                            task__options__release__target__unit=config['unit'],
                                            task__options__release__target__environment=config['environment']).distinct().order_by('-pk')[:50]
        history = list(history)
        proven = defaultdict(list)
        for task_id, host_id in HostResult.objects.filter(
                task_id__in=[row.task_id for row in history],status='success',host_id_snapshot__in=eligible
        ).order_by('host_id_snapshot').values_list('task_id','host_id_snapshot'):
            proven[task_id].append(host_id)
        candidates = []
        for row in history:
            try:
                manifest(row.artifact,config['origin'])
            except ValidationError:
                continue
            if proven[row.task_id]:
                candidates.append({'id':row.pk,'version':row.artifact['version'],'host_ids':proven[row.task_id]})
        busy = ReleaseRun.objects.filter(application=current.application,task__status__in=ACTIVE).exists()
        return Response({'candidates':candidates,'busy':busy})


class ReleasesView(APIView):
    def get(self, request):
        rows = release_rows(request.user)
        targets = []
        busy = set(ReleaseRun.objects.filter(task__status__in=ACTIVE).values_list('application',flat=True)) if request.user.is_superuser else set()
        if request.user.is_superuser:
            saved = {row.application:row for row in ReleaseApplication.objects.all()}
            for name in sorted(legacy_targets().keys() | saved.keys()):
                row = saved.get(name)
                if row and not row.enabled:
                    continue
                try:
                    cfg = normalize_target(row.target if row else legacy_targets()[name])
                except ValidationError:
                    continue
                targets.append({'application':name,'environment':cfg['environment'],'origin':cfg['origin'],
                                'host_ids':cfg['host_ids'],'unit':cfg['unit'],'path':cfg['path'],
                                'port':cfg['port'],'busy':name in busy,'managed':bool(row)})
        response = {'applications':targets}
        if 'page' not in request.query_params:
            return Response({**response,'results':[release_data(row) for row in rows[:50]]})
        if application := request.query_params.get('application','')[:40]:
            rows = rows.filter(application=application)
        if term := request.query_params.get('search','').strip()[:100]:
            rows = rows.filter(Q(application__icontains=term) | Q(artifact__version__icontains=term) |
                               Q(task__owner__username__icontains=term))
        response['overview'] = rows.aggregate(
            awaiting_approval=Count('pk',filter=Q(task__status='awaiting_approval')),
            in_progress=Count('pk',filter=Q(task__status__in=['pending','running'])),
            awaiting_release=Count('pk',filter=Q(task__status='awaiting_release')),
            needs_review=Count('pk',filter=Q(task__status__in=['failed','interrupted'])),
        )
        status = request.query_params.get('status')
        grouped = {'in_progress':['pending','running'],'needs_review':['failed','interrupted']}
        if status in grouped:
            rows = rows.filter(task__status__in=grouped[status])
        elif status in RELEASE_STATUSES:
            rows = rows.filter(task__status=status)
        pager = PageNumberPagination()
        pager.page_size = 15
        page = pager.paginate_queryset(rows,request)
        response['worker_active'] = WorkerLease.objects.filter(name='default',expires_at__gt=timezone.now()).exists()
        return Response({**response,'count':pager.page.paginator.count,'results':[release_data(row) for row in page]})

    @transaction.atomic
    def post(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可提交发布')
        body = request.data
        application = body.get('application')
        if body.get('confirmed') is True:
            admission_lock()
        cfg = release_target(application)
        ids = body.get('host_ids')
        hosts = {host.id: host for host in require_hosts(request.user, ids)}
        if len(ids) != len(hosts) or any(pk not in cfg['host_ids'] or host.environment != cfg['environment']
                                       for pk, host in hosts.items()):
            raise ValidationError('目标主机必须在应用白名单及指定环境内，且不可重复')
        if any(not host.ip_addr or host.asset_status=='retired' or 'windows' in host.os_name.casefold() for host in hosts.values()):
            raise ValidationError('发布目标须为 Linux 主机，且未下线、有连接地址')
        reason = clean_text(body.get('reason'), 500, '发布原因', True)
        rollback = None
        if body.get('rollback_of') is not None or body.get('restore_from') is not None:
            releases = ReleaseRun.objects.filter(application=application)
            if any(type(body.get(key)) is not int for key in ('rollback_of','restore_from')):
                raise ValidationError('回退必须指定来源与目标发布记录')
            rollback = get_object_or_404(releases, pk=body['rollback_of'])
            previous = get_object_or_404(releases, pk=body['restore_from'])
            check_rollback(rollback, previous, ids, cfg)
            artifact = manifest(previous.artifact, cfg['origin'])
        else:
            artifact_id = body.get('artifact_id')
            registry = ReleaseArtifact.objects.filter(application__application=application)
            if artifact_id is not None:
                if type(artifact_id) is not int or artifact_id < 1:
                    raise ValidationError('制品记录 ID 无效')
                stored = get_object_or_404(registry,pk=artifact_id)
                artifact = manifest({'version':stored.version,'url':stored.url,'sha256':stored.sha256},cfg['origin'])
            else:
                artifact = manifest(body,cfg['origin'])
            existing = registry.filter(version=artifact['version']).first()
            if existing and (existing.sha256!=artifact['sha256'] or existing.url!=artifact['url']):
                raise ValidationError('该版本已通过 CI 登记，请选择登记的制品')
            conflict = ReleaseRun.objects.filter(application=application,artifact__version=artifact['version']).exclude(
                artifact__sha256=artifact['sha256'])
            if conflict.exists():
                raise ValidationError('该应用的版本号已绑定其他制品摘要，请使用新版本号')
        remote = {**artifact, 'application': application, 'unit': cfg['unit'], 'port': cfg['port'], 'path': cfg['path']}
        source = SCRIPT_PATH.read_text(encoding='utf-8').replace('main(json.loads(sys.argv[1]))', 'main(' + repr(remote) + ')')
        payload = validate_script({'language':'python','source':source,'name':f'{application} · {artifact["version"]}',
                                   'args':[], 'timeout':150,'concurrency':2})
        payload.pop('source')
        payload['managed'] = {'job_id':None,'version':artifact['version'],'kind':'python','risk':'change','protected':True,
                              'parameters':{},'reason':reason,'verify_service':cfg['unit'],'canary_count':1,
                              'batch_size':4,'failure_limit':1,'targets':[target_snapshot(hosts[pk]) for pk in ids]}
        payload['release'] = {'application':application,'artifact':artifact,'target':cfg,
                              'rollback_of':rollback.pk if rollback else None, 'restore_from':body.get('restore_from')}
        if body.get('confirmed') is not True:
            return Response({'token': signed_plan(request.user.id, source, payload),
                             'application': application, 'artifact': artifact, 'targets':payload['managed']['targets'],
                             'policy':{key:cfg[key] for key in ('unit','port','path','environment')},
                             'strategy':{key:payload['managed'][key] for key in ('canary_count','batch_size','failure_limit')},
                             'concurrency':payload['concurrency'],
                             'warnings':['主机 '+host.name+' 正在维护，请核对变更窗口' for host in hosts.values() if host.asset_status=='maintenance'],
                             'application_busy':ReleaseRun.objects.filter(application=application,task__status__in=ACTIVE).exists()})
        check_plan(body.get('token'), request.user.id, source, payload)
        locked = ExecutionTask.objects.select_for_update().filter(release_run__application=application, status__in=ACTIVE)
        if locked.exists():
            raise ValidationError('该应用已有待处理的发布或回退')
        task = enqueue(request.user, ids, 'script', source, payload, True)
        row = ReleaseRun.objects.create(task=task, application=application, artifact=artifact, rollback_of=rollback)
        return Response(release_data(row), status=202)


def verify_release(task):
    if not get_user_model().objects.filter(pk=task.owner_id,is_active=True,is_superuser=True).exists():
        raise ValidationError('发布申请人已失去管理员权限')
    if Host.objects.filter(pk__in=task.host_ids,asset_status='retired').exists():
        raise ValidationError('存在已下线的发布目标，停止派发')
    spec = task.options['release']
    if release_target(spec['application']) != spec['target']:
        raise ValidationError('发布应用配置已变更，须重新预览提交')
