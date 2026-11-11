from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import secrets

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from host.permissions import require_hosts
from .managed import target_snapshot
from .models import ReleaseApplication, ReleaseArtifact, ReleaseRun
from .releases import SLUG, admission_lock, legacy_targets, manifest, normalize_target, release_target
from .scripts import validate_script
from .task_views import enqueue, serialize

PRECHECK = Path(__file__).with_name('release_precheck.py')


def require_admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied('仅管理员可管理 CI/CD 配置')


def profile_data(row):
    return {'application':row.application, **row.target, 'enabled':row.enabled,'version':row.version,
            'ci_enabled':bool(row.ci_digest),'updated_at':row.updated_at,
            'updated_by':row.updated_by.username}


class ApplicationView(APIView):
    def get(self, request):
        require_admin(request)
        return Response([profile_data(row) for row in ReleaseApplication.objects.select_related('updated_by').order_by('application')])

    @transaction.atomic
    def post(self, request):
        require_admin(request)
        body = request.data
        if not hasattr(body,'get'):
            raise ValidationError('应用配置必须为对象')
        name = body.get('application')
        enabled = body.get('enabled',True)
        if not isinstance(name,str) or not SLUG.fullmatch(name) or type(enabled) is not bool:
            raise ValidationError('应用标识限 40 位小写字母、数字和连字符，启用状态须为布尔值')
        admission_lock()
        row = ReleaseApplication.objects.select_for_update().filter(application=name).first()
        if row and (type(body.get('version')) is not int or body['version'] != row.version):
            raise ValidationError('应用配置已变化，请刷新重试')
        if row is None and ReleaseApplication.objects.count() >= 100:
            raise ValidationError('最多登记 100 个应用')
        target = normalize_target(body)
        if not (row and not enabled and target == row.target):
            hosts = require_hosts(request.user,target['host_ids'])
            if any(host.environment != target['environment'] or host.asset_status=='retired'
                   or not host.ip_addr or 'windows' in host.os_name.casefold() for host in hosts):
                raise ValidationError('目标须为有连接地址、未下线且属于所选环境的 Linux 主机')
        if row:
            row.target, row.enabled, row.updated_by = target, enabled, request.user
            row.version += 1
            row.save()
        else:
            row = ReleaseApplication.objects.create(application=name,target=target,enabled=enabled,updated_by=request.user)
        return Response(profile_data(row),status=200 if row.version>1 else 201)


class ApplicationTokenView(APIView):
    @transaction.atomic
    def post(self, request, application):
        require_admin(request)
        admission_lock()
        row = get_object_or_404(ReleaseApplication.objects.select_for_update(),application=application)
        action = request.data.get('action')
        if action not in ('rotate','revoke'):
            raise ValidationError('请选择生成新令牌或撤销')
        if action == 'rotate' and not row.enabled:
            raise ValidationError('请先启用应用')
        token = secrets.token_urlsafe(32) if action=='rotate' else None
        row.ci_digest = sha256(token.encode()).hexdigest() if token else ''
        row.updated_by = request.user
        row.save(update_fields=['ci_digest','updated_by','updated_at'])
        response = Response({'token':token,'ci_enabled':bool(token)})
        response['Cache-Control'] = 'no-store'
        return response


class CIThrottle(AnonRateThrottle):
    scope = 'release_ci'
    rate = '60/minute'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope':self.scope,'ident':request.META.get('REMOTE_ADDR','unknown')}


def artifact_data(row):
    return {'id':row.pk,'version':row.version,'url':row.url,'sha256':row.sha256,'created_at':row.created_at}


class ArtifactView(APIView):
    def get(self, request, application):
        require_admin(request)
        origin = release_target(application)['origin']
        rows = ReleaseArtifact.objects.filter(application__application=application).order_by('-pk')[:50]
        return Response([artifact_data(row) for row in rows if row.url.startswith(origin + '/')])


class CIIntakeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CIThrottle]

    @transaction.atomic
    def post(self, request, application):
        token = request.headers.get('X-OpsPilot-CI-Token','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            raise PermissionDenied('CI 令牌无效或应用已停用')
        digest = sha256(token.encode()).hexdigest()
        candidate = ReleaseApplication.objects.filter(application=application,enabled=True).only('ci_digest').first()
        if not candidate or not hmac.compare_digest(candidate.ci_digest,digest):
            raise PermissionDenied('CI 令牌无效或应用已停用')
        admission_lock()
        row = ReleaseApplication.objects.select_for_update().get(pk=candidate.pk)
        if not row.enabled or not hmac.compare_digest(row.ci_digest,digest):
            raise PermissionDenied('CI 令牌无效或应用已停用')
        artifact = manifest(request.data,row.target['origin'])
        previous = row.artifacts.filter(version=artifact['version']).first()
        if previous:
            if previous.sha256 != artifact['sha256'] or previous.url != artifact['url']:
                raise ValidationError('该 CI 版本已绑定不同的制品')
            return Response(artifact_data(previous))
        if ReleaseRun.objects.filter(application=application,artifact__version=artifact['version']).exclude(
                artifact__sha256=artifact['sha256']).exists():
            raise ValidationError('该版本已绑定其他制品摘要')
        if row.artifacts.count() >= 1000:
            raise ValidationError('该应用的制品记录已达上限')
        created = ReleaseArtifact.objects.create(application=row,**artifact)
        return Response(artifact_data(created),status=201)


class ReleasePrecheckView(APIView):
    @transaction.atomic
    def post(self, request, application):
        require_admin(request)
        if request.data.get('confirmed') is not True:
            raise ValidationError('请确认将通过 SSH 检查所选主机')
        target = release_target(application)
        ids = request.data.get('host_ids')
        if not isinstance(ids,list) or not 1 <= len(ids) <= 20:
            raise ValidationError('每次可检查 1 到 20 台主机')
        hosts = require_hosts(request.user,ids)
        if len(hosts)!=len(ids) or any(host.pk not in target['host_ids'] or host.environment!=target['environment']
                                       or host.asset_status=='retired' or not host.ip_addr or 'windows' in host.os_name.casefold() for host in hosts):
            raise ValidationError('存在不属于当前发布范围的目标')
        remote = {key:target[key] for key in ('unit','port','path')}
        payload = validate_script({'language':'python','name':f'{application} · 发布预检',
                                   'source':PRECHECK.read_text(encoding='utf-8'),
                                   'args':[json.dumps({'application':application,**remote},separators=(',',':'))],
                                   'timeout':30,'concurrency':2})
        source = payload.pop('source')
        payload['release_precheck'] = {'application':application,'target':target,
                                       'targets':[target_snapshot(host) for host in hosts]}
        return Response(serialize(enqueue(request.user,ids,'script',source,payload,False)),status=202)


def verify_precheck(task, hosts):
    from django.contrib.auth import get_user_model
    spec = task.options['release_precheck']
    if (not get_user_model().objects.filter(pk=task.owner_id,is_active=True,is_superuser=True).exists()
            or release_target(spec['application']) != spec['target']
            or any(host.asset_status=='retired' for host in hosts)
            or [target_snapshot(host) for host in sorted(hosts,key=lambda host:host.pk)]
            != sorted(spec['targets'],key=lambda row:row['id'])):
        raise ValidationError('发布配置或主机范围已变化，请重新提交预检')
