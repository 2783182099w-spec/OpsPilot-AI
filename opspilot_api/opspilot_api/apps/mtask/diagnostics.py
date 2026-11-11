from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Finding, FindingNote
from host.operations import FindingsView
from host.permissions import require_hosts
from .diagnostic_routes import recommendation, source_plan
from .managed import ACTIVE, UNIT, bounded_int, clean_text, target_snapshot
from .models import DiagnosticRun, DiagnosticTree, DiagnosticTreeRevision, ReleaseRun, WorkerLease
from .scripts import validate_script
from . import ssh_baseline
from .task_views import enqueue, serialize, visible_tasks

SCRIPT_PATH = Path(__file__).with_name('diagnostic_script.py')
IDENT = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,29}')
PATH = re.compile(r'/[A-Za-z0-9/_.~-]{0,150}')
CHECKS = ('systemd','http','port','disk','log')
OUTCOMES = ('ok','failed','unknown')
DEFAULT_TREE = {
    'root':'http',
    'nodes':[
        {'id':'http','kind':'http','port':80,'path':'/','ok':'healthy','failed':'unit','unknown':'uncertain'},
        {'id':'unit','kind':'systemd','unit':'nginx.service','ok':'logs','failed':'service_down','unknown':'uncertain'},
        {'id':'logs','kind':'log','unit':'nginx.service','keyword':'error','ok':'check_upstream',
         'failed':'check_logs','unknown':'uncertain'},
    ],
    'conclusions':{'healthy':'本机 HTTP 探测通过','service_down':'服务未运行，请核查退出原因',
                   'check_upstream':'服务运行但 HTTP 检查失败，请检查监听和上游',
                   'check_logs':'近期存在匹配的日志，请进一步核查',
                   'uncertain':'证据不足，请人工确认'},
}


def safe_path(value):
    if not isinstance(value, str) or not PATH.fullmatch(value) or '..' in value.split('/') or value.startswith('//'):
        raise ValidationError('需要合法绝对路径，且不能包含 .. 或查询参数')
    return value


def tree_definition(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('nodes'), list) or not 1 <= len(raw['nodes']) <= 12:
        raise ValidationError('诊断树须有 1～12 个检查')
    leaves = raw.get('conclusions')
    if not isinstance(leaves, dict) or not 1 <= len(leaves) <= 12:
        raise ValidationError('诊断树须有 1～12 个结论')
    if any(not IDENT.fullmatch(key) for key in leaves):
        raise ValidationError('结论 ID 格式无效')
    leaves = {key:clean_text(text,160,'结论',True) for key,text in leaves.items()}
    nodes = {}
    for item in raw['nodes']:
        if not isinstance(item, dict):
            raise ValidationError('检查必须为对象')
        ident, kind = item.get('id'), item.get('kind')
        if not isinstance(ident,str) or not IDENT.fullmatch(ident) or ident in nodes or ident in leaves or kind not in CHECKS:
            raise ValidationError('检查 ID 重复或检查类型无效')
        branches = {key:item.get(key) for key in OUTCOMES}
        if any(not isinstance(value,str) or not IDENT.fullmatch(value) for value in branches.values()):
            raise ValidationError('每个检查须配置通过、异常、待确认三个分支')
        row = nodes[ident] = {'id':ident,'kind':kind,**branches}
        if kind in ('systemd','log'):
            unit = item.get('unit')
            if not isinstance(unit,str) or not UNIT.fullmatch(unit):
                raise ValidationError('服务检查必须指定合法的 systemd 单元')
            row['unit'] = unit
        if kind in ('http','port'):
            row['port'] = bounded_int(item.get('port'),1,65535,'端口')
        if kind in ('http','disk'):
            row['path'] = safe_path(item.get('path'))
        if kind == 'disk':
            row['threshold'] = bounded_int(item.get('threshold'),1,100,'磁盘使用率阈值')
        if kind == 'log':
            row['keyword'] = clean_text(item.get('keyword'),80,'日志关键词',True)

    visited = set()

    def walk(key, path):
        if key in leaves:
            visited.add(key)
            return
        if key not in nodes or key in path:
            raise ValidationError('诊断分支引用不存在或包含环路')
        if key not in visited:
            for branch in OUTCOMES:
                walk(nodes[key][branch], path | {key})
            visited.add(key)

    root = raw.get('root')
    if not isinstance(root,str) or root not in nodes:
        raise ValidationError('须指定一个入口检查')
    walk(root,set())
    if visited != nodes.keys() | leaves.keys():
        raise ValidationError('含无法到达的检查或结论')
    return {'root':root,'nodes':list(nodes.values()),'conclusions':leaves}


def diagnostic_source(tree):
    return SCRIPT_PATH.read_text(encoding='utf-8') + '\nrun_tree(json.loads(' + repr(json.dumps(tree)) + '))\n'


def repair_source(port, path):
    return SCRIPT_PATH.read_text(encoding='utf-8') + '\nraise SystemExit(heal(' + repr({'port':port,'path':path}) + '))\n'


def tree_data(tree):
    return {'id':tree.id,'name':tree.name,'version':tree.version,'definition':tree.definition}


class TreesView(APIView):
    def get(self, request, pk=None):
        trees = DiagnosticTree.objects.order_by('name','id')
        if not pk:
            from .diagnostic_workflows import catalog
            revisions = DiagnosticTreeRevision.objects.order_by('tree_id','-version').values('id','tree_id','version','name','definition')
            return Response({'sample':DEFAULT_TREE,'results':[tree_data(row) for row in trees[:50]],
                             'builtins':catalog(),'revisions':list(revisions[:500])})
        tree = get_object_or_404(trees,pk=pk)
        versions = tree.revisions.order_by('-version').values('version','name','actor__username','created_at','definition')[:20]
        return Response({**tree_data(tree),'revisions':list(versions)})

    @transaction.atomic
    def post(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可以维护诊断树')
        name = clean_text(request.data.get('name'),100,'诊断树名称',True)
        definition = tree_definition(request.data.get('definition'))
        if pk:
            tree = get_object_or_404(DiagnosticTree.objects.select_for_update(),pk=pk)
            if tree.version != request.data.get('version'):
                raise ValidationError('诊断树版本已变化，请重新打开')
            tree.name, tree.definition, tree.version = name, definition, tree.version + 1
            tree.save(update_fields=['name','definition','version'])
        else:
            if DiagnosticTree.objects.count() >= 50:
                raise ValidationError('最多 50 棵诊断树')
            tree = DiagnosticTree.objects.create(name=name,definition=definition)
        DiagnosticTreeRevision.objects.create(tree=tree,actor=request.user,version=tree.version,
                                              name=tree.name,definition=tree.definition)
        return Response(tree_data(tree),status=200 if pk else 201)


class TrialView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        tree = get_object_or_404(DiagnosticTree,pk=pk)
        if request.data.get('confirmed') is not True or request.data.get('version') != tree.version:
            raise ValidationError('诊断树版本已变化或目标未确认')
        host = require_hosts(request.user,[request.data.get('host_id')])[0]
        task = enqueue_incident(request.user,host,diagnostic_source(tree.definition),'diagnosis',False,
                                {'mode':'diagnosis','tree_name':tree.name,'tree_version':tree.version,
                                 'tree':tree.definition,'automatic':False})
        return Response(serialize(task),status=202)


def incident_data(run):
    return {'task_id':run.task_id,'status':run.task.status,'mode':run.mode,'occurrence':run.occurrence,
            'created_at':run.task.created_at,'actor':run.task.owner.username,
            'tree_name':run.task.options['incident'].get('tree_name'),
            'tree_version':run.task.options['incident'].get('tree_version')}


def nginx_tree(probe):
    tree = deepcopy(DEFAULT_TREE)
    tree['nodes'][0].update(port=probe.port,path=probe.path)
    return recommendation('Nginx 服务排查',tree['nodes'],tree['conclusions'])


def recommended_tree(finding, probe=None):
    if probe and probe.enabled and probe.tree_id:
        return probe.tree
    if probe and probe.enabled and probe.unit == 'nginx.service':
        return nginx_tree(probe)
    if probe and probe.enabled:
        nodes = [
            {'id':'http','kind':'http','port':probe.port,'path':probe.path,'ok':'healthy','failed':'port','unknown':'unknown'},
            {'id':'port','kind':'port','port':probe.port,'ok':'listening','failed':'unreachable','unknown':'unknown'},
        ]
        conclusions = {'healthy':'本机 HTTP 检查通过，请对照告警采样时间。',
                       'listening':'端口可达但 HTTP 响应异常，请检查应用或上游。',
                       'unreachable':'本机 HTTP 异常且端口不可达，请检查服务监听。',
                       'unknown':'探测未获得可靠证据，请核查服务与网络。'}
        if UNIT.fullmatch(probe.unit or ''):
            nodes[1]['failed'] = 'unit'
            nodes.append({'id':'unit','kind':'systemd','unit':probe.unit,
                          'ok':'unreachable','failed':'unit_failed','unknown':'unknown'})
            conclusions['unit_failed'] = 'HTTP 和端口均异常，登记的 systemd 单元当前未运行。'
        return recommendation('HTTP 服务与监听排查',nodes,conclusions)
    return source_plan(finding)


def service_probe(finding):
    if finding.source != 'service' or not finding.host_id:
        return None
    from monitoring.models import ServiceCheck
    return ServiceCheck.objects.filter(pk=finding.signal.get('check_id'),host_id=finding.host_id).select_related('host','tree').first()


CHECK_TITLES = {'snapshot':'Linux 主机快照','http':'本机 HTTP 探测','port':'本机 TCP 端口',
                'systemd':'systemd 服务状态','disk':'文件系统容量','log':'近期日志匹配','resource':'资源复检','resource_context':'补充资源证据','scrape':'Prometheus 采集链路',
                'collector':'Node Exporter 采集器','connectivity':'SSH TCP 连通性','inspection':'巡检专项复检'}
RESOURCE_TITLES = {'cpu':'CPU 使用率','memory':'内存使用率','disk':'文件系统使用率',
                   'inode':'文件系统 inode 使用率','load':'一分钟负载'}
CHECK_ADVICE = {
    'http':'核对本机监听、应用日志和反向代理上游。',
    'port':'核对服务监听地址、端口配置和进程状态。',
    'systemd':'检查服务退出原因和近期日志，再决定是否恢复。',
    'log':'按告警时段核对匹配日志的上下文和最近变更。',
    'disk':'先定位占用目录或挂载点，再评估清理或扩容。',
    'scrape':'从 Prometheus 所在网络核对 Exporter 端口、隧道和防火墙。',
    'collector':'核对失败采集器对应的权限、挂载点和 Exporter 日志。',
    'connectivity':'核对 SSH 地址、端口、路由和防火墙。',
}
RESOURCE_ADVICE = {
    'cpu':'查看高 CPU 进程，对照告警时段的负载趋势。',
    'memory':'查看进程 RSS、Swap 与 OOM 记录，确认内存压力来源。',
    'disk':'确认告警对应的挂载点，定位占用目录再评估清理或扩容。',
    'inode':'确认告警对应的挂载点，排查大量小文件或异常增长。',
    'load':'结合 CPU、运行队列与 I/O 等待判断负载来源。',
}


def check_evidence(node,kind,state,evidence):
    metric = node.get('metric')
    title = node.get('title') or (RESOURCE_TITLES.get(metric) if kind=='resource' else None) or CHECK_TITLES.get(kind,kind)
    result = {'kind':kind,'state':state,'evidence':evidence,'title':title}
    if kind in ('http','port') and node.get('port'):
        result['scope'] = '127.0.0.1:' + str(node['port']) + node.get('path','')
    if kind in ('systemd','log') and node.get('unit'):
        result['scope'] = node['unit']
        if kind == 'systemd':
            result['evidence'] = {'active':'运行中','failed':'单元失败','inactive':'未运行'}.get(evidence,evidence)
    if (kind=='resource' and metric in ('disk','inode') and not node.get('all_mounts')) or kind=='disk':
        result['scope'] = node.get('path','/')
    match = re.fullmatch(r'(?:cpu|memory|disk|inode|load) (-?\d+(?:\.\d+)?) / 告警阈值 (\d+(?:\.\d+)?)(?: · 最高挂载点 (.+))?',evidence)
    if kind == 'resource' and match:
        value, threshold, mount = match.groups()
        if mount:
            result['scope'] = mount
        unit = '%' if metric != 'load' else ''
        result.update(observed=value+unit, threshold=threshold+unit)
    advice = RESOURCE_ADVICE.get(metric) or CHECK_ADVICE.get(kind)
    if kind=='inspection':
        check_id = node.get('id','')
        advice = RESOURCE_ADVICE.get(check_id) or {
            'time_sync':'检查 chronyd 或 systemd-timesyncd 的同步状态和上游时钟。',
            'ssh_root':'核对 sshd 的有效 root 登录策略和配置片段。',
            'ssh_password':'核对 sshd 的有效密码认证策略和配置片段。',
            'uid0':'核对 UID 0 账号的用途、来源和变更记录。',
            'firewall':'核对实际生效的防火墙规则及变更记录。',
        }.get(check_id)
        if check_id.startswith('service_'):
            advice = CHECK_ADVICE['systemd']
        elif check_id.startswith('cert_'):
            advice = '确认该证书实际用于哪个服务，核对续期、重载和生效时间。'
    result['advice'] = advice or '请对照告警时段与实际主机状态。'
    return result


def diagnosis_summary(status, checks, conclusion, failure_code):
    if status in ACTIVE:
        return {'outcome':'pending','next_step':'任务结束后会自动刷新结果。'}
    failed = next((row for row in checks if row['state']=='failed'),None)
    unknown = next((row for row in checks if row['state']=='unknown'),None)
    if status != 'completed' or not checks or not conclusion:
        retry = {
            'fingerprint_missing':'核对目标主机的可信 SSH 指纹，再重新排查。',
            'fingerprint_changed':'通过可信渠道核实主机指纹是否发生变更。',
            'authentication_failed':'核对 CMDB 中的 SSH 账号和授权，再重新排查。',
            'connection_failed':'检查 SSH 地址、端口和网络连通性，再重新排查。',
            'connect_timeout':'SSH 连接超时，请检查目标网络和防火墙。',
            'preflight_failed':'检查目标机的 Python 3 和运行依赖后再重试。',
        }
        return {'outcome':'incomplete','next_step':retry.get(failure_code,'检查任务输出和目标连接，补齐证据后重新排查。')}
    if failed:
        return {'outcome':'abnormal','next_step':failed['advice']}
    if unknown:
        return {'outcome':'uncertain','next_step':'有检查缺少可靠数据。核对目标和权限，补采证据后再判断。'}
    if all(row['kind']=='snapshot' for row in checks):
        return {'outcome':'evidence','next_step':'结合告警发生时的趋势判断原因，按需查看进程或日志。'}
    return {'outcome':'clear','next_step':'对照告警采样时间和监控趋势，持续观察是否复发。'}


def workspace_run(run, user):
    data = incident_data(run)
    result = next(iter(run.task.host_results.all()),None)
    output = result.output if result else ''
    metadata = run.task.options['incident']
    nodes = {node['id']:node for node in metadata.get('tree',{}).get('nodes',[])}
    checks = []
    for line in output.splitlines():
        match = re.fullmatch(r'\[([A-Za-z][A-Za-z0-9_-]{0,29})\] ([a-z]+) · (ok|failed|unknown) · (.{1,350})',line)
        if match:
            node, kind, state, evidence = match.groups()
            checks.append({'node':node,**check_evidence(nodes.get(node,{}),kind,state,evidence)})
    if result and not checks:
        for row in result.checks[:12]:
            state = {'pass':'ok','warning':'failed'}.get(row['state'],'unknown')
            check = check_evidence(nodes.get(row['id'],{}),nodes.get(row['id'],{}).get('kind','inspection'),state,
                                   ' · '.join(filter(None,[row.get('summary'),row.get('evidence','')[:300]])))
            checks.append({'node':row['id'],**check,'title':row['title']})
    conclusion = next((line[len('诊断结论：'):][:350] for line in reversed(output.splitlines())
                       if line.startswith('诊断结论：')), '')
    if not conclusion and metadata.get('executor') in ('inspection','tcp','monitor') and checks:
        conclusion = ('专项检查发现异常。' if any(row['state']=='failed' for row in checks) else
                      '专项检查未得到可靠结果。' if any(row['state']=='unknown' for row in checks) else '专项检查已完成，请对照原告警。')
    if result and metadata.get('executor')=='inspection':
        confirmed = next((row.get('summary','') for row in result.checks
                          if row['id']==metadata.get('check_id') and row['id'] in ssh_baseline.ACTIONS), '')
        if confirmed:
            conclusion = confirmed
    can_open_task = user.is_superuser or run.task.owner_id == user.pk
    return {**data,'checks':checks[:12],'conclusion':conclusion,
            'summary':diagnosis_summary(run.task.status,checks,conclusion,result.failure_code if result else '') if run.mode=='diagnosis' else None,
            'finished_at':run.task.finished_at,'automation_scope':metadata.get('automation_scope','policy' if metadata.get('automatic') else 'approval'),
            'escalated_at':run.escalated_at,'output':output[:3500] if can_open_task else '', 'can_open_task':can_open_task}


def readonly_diagnosis(finding):
    run = finding.diagnostic_workflows.filter(
        occurrence=finding.occurrences,status='diagnosed',snapshot__mode='diagnose',
    ).select_related('diagnosis__task').first()
    return run.diagnosis if run else None


class RepairApprovalsView(APIView):
    def get(self, request):
        runs = DiagnosticRun.objects.filter(
            mode='repair', task__status='awaiting_approval', task__in=visible_tasks(request.user),
            finding__in=FindingsView().visible(request.user),
        ).select_related('task__owner','finding__host').order_by('-id')
        paginator = PageNumberPagination()
        paginator.page_size = 20
        rows = paginator.paginate_queryset(runs,request)
        return paginator.get_paginated_response([{
            **incident_data(run),'finding_id':run.finding_id,'title':run.finding.title,
            'host':run.finding.host.name if run.finding.host else '资产已删除',
            'diagnosis_task_id':run.task.options['incident'].get('diagnosis_task_id'),
            'action':ssh_baseline.ACTIONS.get(run.task.options['incident'].get('action'),ssh_baseline.NGINX),
            'can_approve':request.user.is_superuser and run.task.owner_id!=request.user.pk,
        } for run in rows])


class DiagnosisView(APIView):
    def get(self, request, pk):
        finding = get_object_or_404(FindingsView().visible(request.user),pk=pk)
        runs = finding.diagnostic_runs.select_related('task__owner').order_by('-id')
        if request.query_params.get('workspace') != '1':
            return Response([incident_data(run) for run in runs.filter(task__in=visible_tasks(request.user))[:30]])
        from .diagnostic_workflows import owns_alert, workflow_data
        governed = owns_alert(finding)
        manual_run = readonly_diagnosis(finding) if governed else None
        probe = service_probe(finding)
        tree = recommended_tree(finding, probe)
        online = WorkerLease.objects.filter(name='default',expires_at__gt=timezone.now()).exists()
        diagnosis_available = online and bool(finding.host_id) and not finding.host.is_deleted and not runs.filter(
            mode='diagnosis',task__status__in=ACTIVE).exists()
        baseline_action = ssh_baseline.action_for(finding)
        action = ssh_baseline.ACTIONS.get(baseline_action) if baseline_action else ssh_baseline.NGINX if probe else None
        reason = ''
        once_reason = ''
        if request.user.is_superuser:
            if baseline_action:
                try:
                    if not online:
                        raise ValidationError('任务执行器未就绪。')
                    ssh_baseline.check_request(finding,baseline_action)
                except ValidationError as exc:
                    reason = str(exc.detail[0])
            elif governed and not manual_run:
                reason = '本轮由诊断策略处理，审批入口在策略执行卡片。'
            elif not probe:
                reason = '此结论没有登记受控修复动作，可以接手处理，完成后重新诊断。'
            elif not online:
                reason = '任务执行器未就绪。'
            else:
                try:
                    check_repair(finding,probe,automatic=False,diagnostic_run=manual_run)
                except ValidationError as exc:
                    reason = str(exc.detail[0])
                try:
                    check_repair(finding,probe,automatic=True,one_shot=True)
                except ValidationError as exc:
                    once_reason = str(exc.detail[0])
        return Response({
            'recommendation':{'name':tree.name,'version':tree.version,'tree_id':tree.pk,
                              'checks':[{'title':check_evidence(node,node['kind'],'unknown','')['title']} for node in tree.definition['nodes']]} if tree else None,
            'recommendation_reason':'' if tree else '此告警缺少可匹配的指标、检查项或服务端点。请选择对应的诊断模板。',
            'workflow':workflow_data(finding,request.user),
            'pending_approval':next(({
                'task_id':run.task_id,'actor':run.task.owner.username,
                'action':ssh_baseline.ACTIONS.get(run.task.options['incident'].get('action'),ssh_baseline.NGINX),
                'can_approve':request.user.is_superuser and run.task.owner_id!=request.user.pk,
            } for run in runs.filter(mode='repair',occurrence=finding.occurrences,task__status='awaiting_approval')[:1]),None),
            'worker_online':online,'can_diagnose':diagnosis_available,
            'repair':{'available':request.user.is_superuser and not reason,'reason':reason,'action':action,
                      'once_available':request.user.is_superuser and not baseline_action and not governed and not (reason or once_reason),
                      'once_reason':reason or once_reason,
                      'policy':probe.mode if probe else None,
                      'policy_enabled':bool(probe and probe.enabled and probe.mode=='auto' and probe.host.environment=='test'
                                            and getattr(settings,'OPSPILOT_AUTOMATIC_REPAIR',False))},
            'runs':[workspace_run(run,request.user) for run in runs.prefetch_related('task__host_results')[:8]],
        })

    @transaction.atomic
    def post(self, request, pk):
        finding = get_object_or_404(FindingsView().visible(request.user).select_related(None).select_for_update(),pk=pk)
        if request.data.get('confirmed') is not True:
            raise ValidationError('请确认诊断目标')
        require_hosts(request.user,[finding.host_id])
        mode = request.data.get('mode','diagnosis')
        if mode in ('repair','repair_once'):
            if not request.user.is_superuser:
                raise PermissionDenied('仅管理员可提交修复审批')
            baseline_action = ssh_baseline.action_for(finding)
            if baseline_action:
                if mode!='repair':
                    raise ValidationError('SSH 基线变更需要另一名管理员审批')
                run = ssh_baseline.request(finding,baseline_action,request.user)
                return Response(incident_data(run),status=202)
            from .diagnostic_workflows import owns_alert
            governed = owns_alert(finding)
            manual_run = readonly_diagnosis(finding) if governed and mode=='repair' else None
            if governed and not manual_run:
                raise ValidationError('本轮由诊断策略处理，请通过已绑定的结论分支发起审批')
            probe = service_probe(finding)
            if not probe:
                raise ValidationError('此告警没有绑定 HTTP 恢复策略')
            one_shot = mode == 'repair_once'
            escalate = request.data.get('escalate_on_failure',probe.escalate_on_failure) if one_shot else False
            if type(escalate) is not bool:
                raise ValidationError('失败转人工必须明确为启用或关闭')
            run = queue_repair(finding,probe,request.user,automatic=one_shot,one_shot=one_shot,
                               escalate_on_failure=escalate,diagnostic_run=manual_run)
        elif mode == 'diagnosis':
            tree_id = request.data.get('tree_id')
            if tree_id is None:
                tree = recommended_tree(finding, service_probe(finding))
            elif type(tree_id) is int and tree_id > 0:
                tree = get_object_or_404(DiagnosticTree,pk=tree_id)
            else:
                raise ValidationError('请选择有效的诊断树')
            run = queue_diagnosis(finding,tree,request.user)
        else:
            raise ValidationError('未知处理方式')
        return Response(incident_data(run),status=202)


def queue_diagnosis(finding, tree, actor, automatic=False):
    if finding.diagnostic_runs.filter(mode='diagnosis', task__status__in=ACTIVE).exists():
        raise ValidationError('该告警已有运行中的诊断')
    if automatic and finding.diagnostic_runs.filter(mode='diagnosis',occurrence=finding.occurrences).exists():
        raise ValidationError('本次告警已经诊断过')
    if tree is None:
        raise ValidationError('此告警缺少可匹配的诊断依据，请选择模板')
    executor = getattr(tree,'executor','ssh')
    if executor!='ssh':
        return create_special_diagnosis(finding,tree,actor,automatic)
    source = diagnostic_source(tree.definition)
    return create_incident(finding,actor,source,'diagnosis',False,
                           {'tree_name':tree.name,'tree_version':tree.version,'tree':tree.definition,
                            'automatic':automatic})


def create_special_diagnosis(finding, tree, actor, automatic):
    host = require_hosts(actor,[finding.host_id])[0]
    options = {**tree.metadata.get('inspection_options',{}),'incident':{
        'mode':'diagnosis','finding_id':finding.pk,'occurrence':finding.occurrences,
        'tree_name':tree.name,'tree_version':tree.version,'tree':tree.definition,
        'automatic':automatic,'executor':tree.executor,'check_id':tree.metadata.get('check_id'),
        'metric':tree.metadata.get('metric'),
    }}
    task = enqueue(actor,[host.pk],'inspection' if tree.executor=='inspection' else 'probe','',options,False)
    run = DiagnosticRun.objects.create(finding=finding,occurrence=finding.occurrences,task=task,mode='diagnosis')
    FindingNote.objects.create(finding=finding,actor=actor,text=f'已创建专项诊断任务 #{task.pk}')
    return run


def repair_guard(finding, probe, automatic, one_shot=False):
    now = timezone.now()
    if (not probe.enabled or finding.source != 'service' or finding.signal.get('check_id') != probe.pk
            or probe.host_id != finding.host_id or probe.unit != 'nginx.service' or probe.mode not in ('approval','auto')):
        raise ValidationError('当前 HTTP 探测未配置可审批的 Nginx 修复策略。')
    if finding.condition != 'active':
        raise ValidationError('告警已恢复或状态待确认，暂不可提交修复。')
    if (finding.silenced_until and finding.silenced_until > now) or probe.host.asset_status == 'maintenance':
        raise ValidationError('当前处于维护窗口，修复已暂停。')
    if probe.host.is_deleted or probe.host.asset_status == 'retired':
        raise ValidationError('目标资产已下线，修复已暂停。')
    if probe.state != 'down' or not probe.last_checked_at or probe.last_checked_at < now-timedelta(seconds=120):
        raise ValidationError('最近的探测未确认服务持续异常，等待重新采样。')
    if automatic and ((not one_shot and probe.mode != 'auto') or probe.host.environment != 'test'
                      or not getattr(settings,'OPSPILOT_AUTOMATIC_REPAIR',False)):
        raise ValidationError('自动修复总开关须开启，且目标必须属于测试环境。')


def check_repair(finding, probe, automatic, one_shot=False, diagnostic_run=None):
    repair_guard(finding,probe,automatic,one_shot)
    diagnosis = diagnostic_run or finding.diagnostic_runs.filter(mode='diagnosis',occurrence=finding.occurrences,
                                                task__status='completed').select_related('task').last()
    if not diagnosis:
        raise ValidationError('先为本轮告警完成诊断并核对证据')
    if automatic or diagnostic_run:
        checked_at = diagnosis.task.finished_at or diagnosis.task.created_at
        if checked_at < timezone.now()-timedelta(minutes=15):
            raise ValidationError('诊断已超过 15 分钟，请重新排查后再授权自动处理。')
        evidence = diagnosis.task.host_results.values_list('output',flat=True).first() or ''
        if '诊断结论：' not in evidence or not re.search(r'^\[[A-Za-z][A-Za-z0-9_-]{0,29}\] (?:http|systemd) · failed · ',evidence,re.M):
            raise ValidationError('诊断未提供 HTTP 或 systemd 异常证据，需人工核对。')
    repair_runs = DiagnosticRun.objects.filter(finding__host_id=probe.host_id,mode='repair')
    if (repair_runs.filter(task__status__in=ACTIVE).exists()
            or repair_runs.filter(task__created_at__gte=timezone.now()-timedelta(minutes=30)).exists()
            or finding.diagnostic_runs.filter(mode='repair',occurrence=finding.occurrences).exists()):
        raise ValidationError('本次告警已提交修复，或目标仍在 30 分钟冷却期')

def queue_repair(finding, probe, actor, automatic, one_shot=False, escalate_on_failure=None,
                 workflow=None, diagnostic_run=None):
    check_repair(finding,probe,automatic,one_shot or bool(workflow),diagnostic_run)
    if not automatic and not diagnostic_run:
        diagnostic_run = finding.diagnostic_runs.filter(
            mode='diagnosis',occurrence=finding.occurrences,task__status='completed',
        ).select_related('task').last()
    scope = 'workflow' if workflow else 'once' if one_shot else 'policy' if automatic else 'approval'
    if escalate_on_failure is None:
        escalate_on_failure = automatic and probe.escalate_on_failure
    return create_incident(finding,actor,repair_source(probe.port,probe.path),'repair',not automatic,
                           {'probe_id':probe.pk,'probe_version':probe.version,'automatic':automatic,
                            'automation_scope':scope,'escalate_on_failure':escalate_on_failure,'workflow':workflow,
                            'diagnosis_task_id':diagnostic_run.task_id if diagnostic_run else None})


@transaction.atomic
def escalate_repair(task, reason):
    spec = task.options.get('incident',{})
    if spec.get('mode') != 'repair' or not spec.get('escalate_on_failure'):
        return
    finding = Finding.objects.select_for_update().filter(
        pk=spec['finding_id'],occurrences=spec['occurrence']).exclude(condition='recovered').first()
    if not finding:
        return
    now = timezone.now()
    if DiagnosticRun.objects.filter(task=task,escalated_at__isnull=True).update(escalated_at=now):
        Finding.objects.filter(pk=finding.pk).update(manual_required=True)
        FindingNote.objects.create(finding=finding,text=f'修复任务 #{task.pk} 转人工：{reason}'[:2000])


def create_incident(finding, actor, source, mode, approval, metadata):
    host = require_hosts(actor,[finding.host_id])[0]
    spec = {**metadata,'mode':mode,'finding_id':finding.pk,'occurrence':finding.occurrences}
    task = enqueue_incident(actor,host,source,mode,approval,spec)
    run = DiagnosticRun.objects.create(finding=finding,occurrence=finding.occurrences,task=task,mode=mode)
    FindingNote.objects.create(finding=finding,actor=actor,text=f'已创建{"诊断" if mode=="diagnosis" else "恢复"}任务 #{task.pk}')
    return run


def enqueue_incident(actor, host, source, mode, approval, metadata):
    payload = validate_script({'language':'python','source':source,'name':'告警诊断' if mode=='diagnosis' else ssh_baseline.ACTIONS.get(metadata.get('action'),ssh_baseline.NGINX)['name'],
                               'timeout':95,'concurrency':1})
    payload.pop('source')
    payload['managed'] = {'job_id':None,'version':metadata.get('tree_version',1),'kind':'python',
                          'risk':'read_only' if mode=='diagnosis' else 'change','protected':approval,
                          'parameters':{},'reason':f'告警 #{metadata["finding_id"]} · 第 {metadata["occurrence"]} 次' if metadata.get('finding_id') else '只读试跑',
                          'verify_service':'','canary_count':1,'batch_size':1,'failure_limit':1,
                          'targets':[target_snapshot(host)]}
    payload['incident'] = metadata
    return enqueue(actor,[host.pk],'script',source,payload,approval)


def verify_incident(task):
    spec = task.options['incident']
    if spec['mode'] != 'repair':
        return
    if not get_user_model().objects.filter(pk=task.owner_id,is_active=True,is_superuser=True).exists():
        raise ValidationError('修复申请人已失去管理员权限')
    finding = Finding.objects.select_related('host').filter(pk=spec['finding_id'],occurrences=spec['occurrence']).first()
    if spec.get('action') in ssh_baseline.ACTIONS:
        return ssh_baseline.verify(task,finding)
    from monitoring.service_checks import ServiceCheck
    probe = ServiceCheck.objects.select_related('host').filter(pk=spec['probe_id']).first()
    if not probe or not finding or probe.version != spec['probe_version']:
        raise ValidationError('探测配置或告警轮次已变化，停止派发')
    if spec.get('workflow'):
        from .diagnostic_workflows import validate_workflow
        validate_workflow(task, finding, probe)
    repair_guard(finding,probe,spec['automatic'],spec.get('automation_scope') in ('once','workflow'))
    if any(probe.host_id in release.task.host_ids and release.task.options['release']['target']['unit'] == probe.unit
           for release in ReleaseRun.objects.filter(task__status__in=ACTIVE).select_related('task')):
        raise ValidationError('目标服务正在发布，修复暂停')
    if task.command != repair_source(probe.port,probe.path):
        raise ValidationError('恢复脚本已变更，须重新提交')
