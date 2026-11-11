import csv
from collections import Counter
from django.http import FileResponse
import tempfile
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from host.permissions import require_hosts
from opspilot_api.utils.redaction import redact
from .models import ExecutionTask, HostResult, CmdTemplate, WorkerLease
from .inspection import validate_options
from .template_tools import render
from .scripts import validate_script,verify_preview,preview_token
from .models import JobScript
from .managed import ACTIVE, verify_targets


def visible_tasks(user):
    return ExecutionTask.objects.all() if user.is_superuser else ExecutionTask.objects.filter(owner=user)


def result_data(row):
    return {'host_info': row.host_info, 'status': row.status, 'res_code': row.exit_code, 'res_data': row.output, 'checks': row.checks, 'started_at': row.started_at, 'finished_at': row.finished_at, 'stages':row.stages, 'failure_code':row.failure_code, 'may_have_run':row.may_have_run, 'batch':row.batch}


def serialize(task, detail=False, page=1, result_status="", host_search="", check_state="", failure_code=""):
    data = {'id': task.id, 'kind': task.kind, 'owner_name': task.owner.username, 'command': redact(task.command if detail or task.kind!='script' else task.command[:200]), 'host_ids': task.host_ids, 'status': task.status, 'total': len(task.host_ids), 'done': task.completed_hosts or len(task.results), 'created_at': task.created_at, 'started_at': task.started_at, 'finished_at': task.finished_at, 'cancel_requested': task.cancel_requested, 'results': []}
    if task.kind=='script':
        data['script_meta'] = {k:task.options.get(k) for k in ('name','language','sha256','timeout','concurrency','script_id','script_version')}
    if task.options.get('managed'):
        data['managed_job']={k:task.options['managed'].get(k) for k in ('job_id','version','risk','protected','verify_service','canary_count','batch_size','failure_limit')}
        data.update(rollout_phase=task.rollout_phase,stop_reason=task.stop_reason,released_at=task.released_at)
    if task.options.get('release'):
        data['release'] = {k:task.options['release'][k] for k in ('application','artifact','rollback_of')}
    if task.options.get('incident'):
        data['incident'] = {k:v for k,v in task.options['incident'].items() if k != 'tree'}
    if detail:
        if task.options.get('managed'):
            data['approved_by']=task.approved_by.username if task.approved_by_id else None
            data['released_by']=task.released_by.username if task.released_by_id else None
            data['approved_at']=task.approved_at
        queryset = task.host_results.order_by('id')
        all_rows = queryset
        check_counts = Counter(check.get('state', 'unknown') for checks in all_rows.values_list('checks', flat=True) for check in checks)
        data['check_summary'] = dict(check_counts)
        data['summary'] = dict(Counter(all_rows.values_list('status', flat=True)))
        if result_status:
            queryset = queryset.filter(status=result_status)
        if failure_code:
            queryset=queryset.filter(failure_code=failure_code)
        if host_search or check_state:
            ids = [row.id for row in queryset.only("id", "host_info", "checks") if (not host_search or host_search.lower() in str(row.host_info).lower()) and (not check_state or any(c.get('state') == check_state for c in row.checks))]
            queryset = queryset.filter(id__in=ids)
        count = queryset.count()
        safe_options = {**task.options}
        if task.kind=='script':
            safe_options['args'] = [redact(a) for a in safe_options.get('args',[])]
        if safe_options.get('managed'):
            managed=safe_options['managed']
            safe_options['managed']={**managed,'reason':redact(managed['reason']),
                                     'parameters':{k:redact(v) for k,v in managed['parameters'].items()}}
        data.update(results=[result_data(row) for row in queryset[(page-1)*20:page*20]] if count else ([] if task.host_results.exists() else task.results), results_count=count if task.host_results.exists() else len(task.results), results_page=page, options=safe_options)
    return data


def submit(user, data):
    ids = data.get('host_ids')
    require_hosts(user, ids)
    kind = data.get('kind', 'command')
    if kind not in ('command', 'inspection', 'probe', 'script'):
        raise ValidationError('未知任务类型')
    options = validate_options(kind, data.get('options', {})) if kind in ('inspection','probe') else {}
    cmd = data.get('cmd', '')
    if kind == 'script':
        payload = validate_script(data.get('script'))
        verify_preview(data.get('preview_token'),payload,ids)
        cmd = payload.pop('source')
        options = payload
        if data.get('script_id'):
            if type(data['script_id']) is not int:
                raise ValidationError('脚本库 ID 无效')
            library = get_object_or_404(JobScript,pk=data['script_id'],is_deleted=False)
            if data.get('script_version')!=library.version or cmd!=library.source or payload['language']!=library.language:
                raise ValidationError('脚本库版本或内容已变化，请重新加载、预览或作为临时脚本提交')
            options.update(script_id=library.id,script_version=library.version)

    if kind == 'command' and data.get('template_id'):
        template = get_object_or_404(CmdTemplate, pk=data['template_id'], is_deleted=False)
        if data.get('template_version') != template.version:
            raise ValidationError('模板版本已变化，请重新确认')
        cmd = render(template, data.get('parameters', {}))
        options = {'template_id': template.id, 'template_version': template.version}
    if kind == 'command' and (not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 10000):
        raise ValidationError('命令必须为 1 到 10000 字符')
    if data.get('confirmed') is not True:
        raise ValidationError('请确认目标主机和操作内容')
    return enqueue(user,ids,kind,cmd if kind in ('command','script') else '',options,
                   kind in ('command','script') and not user.is_superuser)


def enqueue(user, ids, kind, command, options, approval):
    if ExecutionTask.objects.filter(owner=user, status__in=ACTIVE).count() >= 5:
        raise ValidationError('每人最多同时保留 5 个待处理任务')
    if ExecutionTask.objects.filter(status__in=ACTIVE).count() >= 100:
        raise ValidationError('队列已满，请稍后提交')
    task=ExecutionTask.objects.create(owner=user,host_ids=list(dict.fromkeys(ids)),command=command,
        kind=kind,options=options,status='awaiting_approval' if approval else 'pending')
    if options.get('managed'):
        HostResult.objects.bulk_create([HostResult(task=task,host_id_snapshot=row['id'],host_info=row,
                                                   may_have_run=False)
                                       for row in options['managed']['targets']])
    return task


def approve(user, task):
    if not user.is_superuser:
        raise PermissionDenied('仅管理员可以审批')
    hosts=require_hosts(task.owner, task.host_ids)
    if task.options.get('managed'):
        if task.options['managed']['protected'] and user.id==task.owner_id:
            raise PermissionDenied('受控变更必须由其他管理员审批')
        verify_targets(task,hosts)
    if task.options.get('incident',{}).get('mode') == 'repair':
        from .diagnostics import verify_incident
        verify_incident(task)
    if task.options.get('release'):
        from .releases import verify_release
        verify_release(task)
    if not ExecutionTask.objects.filter(pk=task.pk, status='awaiting_approval').update(status='pending', approved_by=user, approved_at=timezone.now()):
        raise ValidationError('任务状态已变化')
    task.refresh_from_db()
    return task


class TasksView(APIView):
    def get(self, request, pk=None):
        queryset = visible_tasks(request.user)
        if pk is not None:
            try:
                page = int(request.query_params.get('results_page', 1))
            except ValueError:
                raise ValidationError('结果页码无效')
            if not 1 <= page <= 10000:
                raise ValidationError('结果页码无效')
            return Response(serialize(get_object_or_404(queryset.select_related('owner'), pk=pk), True, page, request.query_params.get('result_status', ''), request.query_params.get('host_search', '')[:100], request.query_params.get('check_state', ''), request.query_params.get('failure_code','')))
        if request.query_params.get('incident_mode') == 'diagnosis':
            queryset = queryset.filter(options__incident__mode='diagnosis')
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        for field in ('kind',):
            if request.query_params.get(field):
                queryset = queryset.filter(**{field: request.query_params[field]})
        search = request.query_params.get('search', '')[:100]
        if search:
            query = Q(command__icontains=search) | Q(owner__username__icontains=search)
            if search.isdigit():
                query |= Q(pk=int(search))
            queryset = queryset.filter(query)
        for name, lookup in [('date_from', 'created_at__date__gte'), ('date_to', 'created_at__date__lte')]:
            if request.query_params.get(name):
                try:
                    value = parse_date(request.query_params[name])
                except ValueError:
                    value = None
                if value is None:
                    raise ValidationError('日期格式须为 YYYY-MM-DD')
                queryset = queryset.filter(**{lookup: value})
        if 'page' in request.query_params:
            from rest_framework.pagination import PageNumberPagination
            paginator = PageNumberPagination()
            paginator.page_size = 20
            rows = paginator.paginate_queryset(queryset.select_related('owner').order_by('-id'), request)
            return paginator.get_paginated_response([serialize(task) for task in rows])
        return Response([serialize(task) for task in queryset.select_related('owner').order_by('-id')[:100]])

    def post(self, request, pk=None):
        if pk is not None:
            return Response(serialize(approve(request.user, get_object_or_404(visible_tasks(request.user), pk=pk))))
        return Response(serialize(submit(request.user, request.data)), status=202)


class TaskActionView(APIView):
    def post(self, request, pk):
        task = get_object_or_404(visible_tasks(request.user), pk=pk)
        action = request.data.get('action')
        if action == 'approve':
            return Response(serialize(approve(request.user, task)))
        if action == 'release':
            managed=task.options.get('managed')
            if not managed or request.data.get('confirmed') is not True or task.status!='awaiting_release':
                raise ValidationError('该任务未在等待后续批次放行')
            if managed['protected'] and (not request.user.is_superuser or request.user.id==task.owner_id):
                raise PermissionDenied('受控变更须由其他管理员放行')
            verify_targets(task,require_hosts(task.owner,task.host_ids))
            if task.options.get('release'):
                from .releases import verify_release
                verify_release(task)
            if not ExecutionTask.objects.filter(pk=pk,status='awaiting_release',cancel_requested=False).update(
                    status='pending',rollout_phase='remainder',released_by=request.user,released_at=timezone.now()):
                raise ValidationError('任务状态已变化')
            task.refresh_from_db()
            return Response(serialize(task))
        if action == 'cancel':
            if task.status not in ('pending','running','awaiting_approval','awaiting_release'):
                raise ValidationError('任务已经结束')
            if task.status == 'running':
                ExecutionTask.objects.filter(pk=pk, status='running').update(cancel_requested=True)
            else:
                changed = ExecutionTask.objects.filter(pk=pk, status__in=['pending','awaiting_approval','awaiting_release']).update(status='cancelled', cancel_requested=True, finished_at=timezone.now())
                if changed and task.options.get('managed'):
                    task.host_results.filter(status='queued').update(status='skipped',may_have_run=False,
                         failure_code='cancelled_before_dispatch',output='取消后未派发',finished_at=timezone.now())
                    ExecutionTask.objects.filter(pk=pk).update(completed_hosts=task.host_results.count())
                if not changed:
                    ExecutionTask.objects.filter(pk=pk, status='running').update(cancel_requested=True)
            task.refresh_from_db()
            return Response(serialize(task))
        if action == 'reject':
            if not request.user.is_superuser:
                raise PermissionDenied('仅管理员可以驳回')
            reason = request.data.get('reason', '')
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                raise ValidationError('请填写 1 到 500 字驳回理由')
            now=timezone.now()
            safe_reason=redact(reason)
            managed=task.options.get('managed')
            updates={'stop_reason':safe_reason[:300]} if managed else {}
            if not ExecutionTask.objects.filter(pk=pk, status='awaiting_approval').update(
                    status='rejected',finished_at=now,results=[{'error':safe_reason}],**updates):
                raise ValidationError('任务状态已变化')
            if managed:
                task.host_results.filter(status='queued').update(status='skipped',failure_code='not_dispatched',
                    may_have_run=False,output='审批驳回，未派发',finished_at=now)
                ExecutionTask.objects.filter(pk=pk).update(completed_hosts=task.host_results.count())
            return Response({'message':'已驳回'})
        if action == 'retry':
            if task.options.get('release_precheck'):
                raise ValidationError('请从发布应用重新提交预检')
            if task.options.get('release') or task.options.get('incident'):
                raise ValidationError('请从发布流水线或告警详情重新评估目标和提交')
            if task.status not in ('completed','failed','interrupted','cancelled'):
                raise ValidationError('只能重新提交已结束的任务')
            if request.data.get('confirmed') is not True:
                raise ValidationError('请确认远端状态，重试可能重复执行命令')
            rows = task.host_results.all()
            if rows.exists():
                ids = list(rows.exclude(status='success').values_list('host_id_snapshot', flat=True))
            else:
                ids = task.host_ids
            if not ids:
                raise ValidationError('没有需要重试的主机')
            retry = {'host_ids': ids, 'kind': task.kind, 'cmd': task.command, 'options': task.options, 'confirmed': True}
            if task.options.get('managed'):
                requested=request.data.get('host_ids')
                if requested is not None:
                    if not isinstance(requested,list) or not requested or any(type(i) is not int or i not in ids for i in requested):
                        raise ValidationError('只能选择未成功的原目标主机')
                    ids=list(dict.fromkeys(requested))
                if rows.filter(host_id_snapshot__in=ids).exclude(may_have_run=False).exists() and request.data.get('acknowledge_remote') is not True:
                    raise ValidationError('存在可能已执行的主机，请先核实远端状态再确认重试')
                selected=require_hosts(request.user,ids)
                verify_targets(task,selected)
                original=task.options['managed']
                snapshots={x['id']:x for x in original['targets']}
                options={**task.options,'managed':{**original,'targets':[snapshots[i] for i in ids],
                    'reason':original['reason'],'retry_of':task.id}}
                return Response(serialize(enqueue(request.user,ids,'script',task.command,options,
                    original['protected'] or not request.user.is_superuser)),status=202)
            if task.kind=='script':
                payload = validate_script({**task.options,'source':task.command})
                retry.update(script=payload,preview_token=preview_token(payload,ids))
            return Response(serialize(submit(request.user, retry)), status=202)
        raise ValidationError('未知操作')


class TaskReportView(APIView):
    def get(self, request, pk):
        task = get_object_or_404(visible_tasks(request.user), pk=pk)
        spool = tempfile.SpooledTemporaryFile(max_size=512*1024)
        class Buffer:
            def write(self, value):
                return spool.write(value.encode('utf-8'))
        try:
            spool.write('\ufeff'.encode('utf-8'))
            writer = csv.writer(Buffer())
            def safe(value):
                value = str(value)
                return "'" + value if value.lstrip().startswith(('=','+','-','@')) else value
            managed=bool(task.options.get('managed'))
            header=['任务','主机','地址','执行状态','检查项','结论','说明','证据']
            writer.writerow(header+(['批次','失败分类','是否尝试执行','退出码','阶段'] if managed else []))
            for row in task.host_results.iterator(chunk_size=50):
                for check in row.checks or [{'title': '命令执行', 'state': row.status, 'summary': '', 'evidence': row.output}]:
                    values=[task.id,row.host_info.get('name',''),row.host_info.get('ip_addr',''),row.status,check['title'],check['state'],check['summary'],check['evidence']]
                    if managed:
                        values.extend([row.batch,row.failure_code,{True:'已尝试',False:'否',None:'未知'}[row.may_have_run],
                            row.exit_code if row.exit_code is not None else '',
                            ' | '.join(f'{s["name"]}:{s["status"]} ({s.get("elapsed_ms",0)}ms)' for s in row.stages)])
                    writer.writerow([safe(value) for value in values])
            spool.seek(0)
            return FileResponse(spool, as_attachment=True, filename=f'task-{pk}-report.csv', content_type='text/csv; charset=utf-8')
        except Exception:
            spool.close()
            raise


class WorkerStatusView(APIView):
    def get(self, request):
        lease = WorkerLease.objects.filter(name='default').first()
        return Response({'active': bool(lease and lease.expires_at > timezone.now()), 'heartbeat_at': lease.heartbeat_at if lease else None, 'pending': visible_tasks(request.user).filter(status='pending').count(), 'concurrency': 4, 'redis_required': False})
