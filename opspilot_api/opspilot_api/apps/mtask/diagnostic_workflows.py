
from datetime import timedelta
from functools import lru_cache
import re
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Finding, FindingNote
from host.permissions import require_hosts
from . import diagnostics as diagnosis
from .diagnostic_routes import RESOURCE_NAMES
from .managed import ACTIVE
from .models import DiagnosticPolicy, DiagnosticTreeRevision, DiagnosticWorkflowRun

ACTION = 'nginx_start'
SOURCES = ('monitor', 'inspection', 'service', 'external')
MODES = ('diagnose', 'approval', 'auto')
TRIGGERS = ('event', 'scheduled', 'both')
OPEN = ('diagnosing', 'repairing', 'verifying')


@lru_cache(maxsize=1)
def catalog():
    from .diagnostic_routes import source_plan
    from .inspection import rules

    items = [
        ('nginx', 'Nginx 服务故障', '服务探测', ['本机 HTTP', 'systemd 状态', '近期错误日志'], ACTION),
        ('http', 'HTTP 服务排查', '服务探测', ['HTTP 状态', '端口监听', '已登记的 systemd 单元'], ''),
        ('ssh', 'SSH 连通性复检', '巡检', ['TCP 连接'], ''),
        ('up', 'Prometheus 采集链路排查', '监控', ['近期采样时间', '采集目标 up 状态'], ''),
        ('collector_failures', 'Node Exporter 采集器排查', '监控', ['采集链路状态', '失败采集器'], ''),
    ]
    for metric, name in RESOURCE_NAMES.items():
        plan = source_plan(SimpleNamespace(source='monitor', signal={'metric':metric,'threshold':1}))
        labels = {'resource':'实时指标', 'resource_context':'资源上下文'}
        items.append((metric, name, '监控', [labels[node['kind']] for node in plan.definition['nodes']], ''))
    for check, title, *_ in rules({'profile':'acceptance','services':[],'certificates':[]}):
        if check != 'connectivity':
            items.append((check, title+'复检', '巡检', [title], check if check in diagnosis.ssh_baseline.ACTIONS else ''))
    items += [('service-unit', '巡检服务状态复检', '巡检', ['任务登记的 systemd 单元'], ''),
              ('certificate', '巡检证书有效期复检', '巡检', ['任务登记的证书文件'], '')]
    return [{'key':source+':'+key,'name':name,'source':source,'checks':checks,'action':action,
             'branches':diagnosis.DEFAULT_TREE['conclusions'] if key=='nginx' else {}}
            for key,name,source,checks,action in items]


def policy_data(row):
    return {'id':row.pk,'name':row.name,'enabled':row.enabled,'version':row.version,
            **row.config}


def policy_config(raw, actor):
    from monitoring.models import ServiceCheck

    source, mode, trigger = (raw.get(field) for field in ('source','mode','trigger'))
    if source not in SOURCES or mode not in MODES or trigger not in TRIGGERS:
        raise ValidationError('请选择有效的告警来源、处理方式和触发方式')
    host_ids = raw.get('host_ids')
    if (not isinstance(host_ids,list) or not 1<=len(host_ids)<=300
            or any(type(pk) is not int or pk<1 for pk in host_ids)):
        raise ValidationError('请选择 1–300 台已授权资产')
    host_ids = sorted(set(host_ids))
    hosts = require_hosts(actor,host_ids)
    signal = raw.get('signal','')
    if not isinstance(signal,str) or len(signal)>80:
        raise ValidationError('匹配值须为 80 字符以内的指标、检查项或探测编号')
    signal = signal.strip()
    if source=='service' and signal and (not signal.isascii() or not signal.isdecimal() or not 1<=int(signal)<=2**63-1):
        raise ValidationError('HTTP 告警的匹配值须为探测编号')
    if source=='service' and signal:
        signal = str(int(signal))
    interval = raw.get('interval',15)
    if type(interval) is not int or not 15<=interval<=1440:
        raise ValidationError('定时间隔须为 15–1440 分钟')
    fallback = raw.get('fallback',True)
    if type(fallback) is not bool:
        raise ValidationError('请明确是否启用转人工')
    tree = raw.get('tree','source')
    revision = raw.get('revision')
    if tree not in ('source','nginx','custom'):
        raise ValidationError('请选择诊断树')
    selected = None
    if tree=='custom':
        selected = DiagnosticTreeRevision.objects.filter(pk=revision).first() if type(revision) is int else None
        if selected is None:
            raise ValidationError('请选择有效的自建诊断树版本')
    else:
        revision = None
    if tree=='nginx' and source!='service':
        raise ValidationError('Nginx 诊断树须绑定 HTTP 服务告警')
    branch = raw.get('branch','') if mode!='diagnose' else ''
    if mode!='diagnose' and source=='inspection':
        if mode!='approval' or signal not in diagnosis.ssh_baseline.ACTIONS or tree!='source' or branch!='confirmed_violation':
            raise ValidationError('SSH 基线只支持按来源运行专项诊断，命中已确认异常后转独立审批')
        if any(host.environment!='test' or host.username!='root' for host in hosts):
            raise ValidationError('SSH 基线审批策略仅支持 root 密钥纳管的测试资产')
    elif mode!='diagnose':
        if source!='service' or tree=='source' or not signal or len(host_ids)!=1 or not isinstance(branch,str):
            raise ValidationError('请绑定单台资产、Nginx HTTP 探测和一个确定的修复结论')
        probe = ServiceCheck.objects.filter(pk=int(signal),host_id=host_ids[0],unit='nginx.service',
                                           enabled=True,mode__in=['approval','auto']).first()
        if not probe:
            raise ValidationError('请先为对应资产启用 Nginx HTTP 恢复探测')
        signal = str(probe.pk)
        definition = selected.definition if selected else diagnosis.nginx_tree(probe).definition
        if not safe_repair_branch(definition,branch,probe):
            raise ValidationError('通向所选结论的每条路径都须确认对应 HTTP 异常且 nginx.service 未运行')
    return {'source':source,'host_ids':host_ids,'signal':signal,'tree':tree,'revision':revision,
            'mode':mode,'trigger':trigger,'interval':interval,'branch':branch,'fallback':fallback}


class PolicyView(APIView):
    def get(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可管理诊断策略')
        return Response([policy_data(row) for row in DiagnosticPolicy.objects.order_by('id')])

    @transaction.atomic
    def post(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员可管理诊断策略')
        row = get_object_or_404(DiagnosticPolicy.objects.select_for_update(),pk=pk) if pk else DiagnosticPolicy()
        if not pk and DiagnosticPolicy.objects.count()>=100:
            raise ValidationError('最多可维护 100 条诊断策略')
        if pk and request.data.get('version') != row.version:
            raise ValidationError('策略已被更新，请刷新后重试')
        name, enabled = request.data.get('name'), request.data.get('enabled',False)
        if not isinstance(name,str) or not 1<=len(name.strip())<=100 or type(enabled) is not bool:
            raise ValidationError('请输入策略名称并选择启用状态')
        # Let an administrator disable an obsolete strategy even if its probe has been removed.
        row.config = row.config if pk and request.data.get('disable_only') is True and not enabled else policy_config(request.data,request.user)
        row.name, row.enabled, row.actor = name.strip(), enabled, request.user
        row.version += 1 if pk else 0
        row.next_scan_at = None
        row.save()
        return Response(policy_data(row),status=200 if pk else 201)


def signal_value(finding):
    signal = finding.signal
    if finding.source=='service':
        return str(signal.get('check_id',''))
    if finding.source=='inspection':
        return str(signal.get('check_id') or finding.dedupe_key.split(':',2)[-1])
    return str(signal.get('metric',''))


def matches(policy, finding):
    cfg = policy.config
    return (finding.host_id in cfg['host_ids'] and finding.source==cfg['source']
            and (not cfg['signal'] or signal_value(finding)==cfg['signal']))


def owns_alert(finding):
    return (finding.diagnostic_workflows.filter(occurrence=finding.occurrences).exists()
            or any(matches(policy,finding) for policy in DiagnosticPolicy.objects.filter(enabled=True)))


def finish(run, status, reason):
    run.status, run.reason = status, reason[:250]
    run.save(update_fields=['status','reason','branch','updated_at'])
    if status=='manual' and run.snapshot['fallback']:
        Finding.objects.filter(pk=run.finding_id,occurrences=run.occurrence).update(manual_required=True)
        FindingNote.objects.create(finding_id=run.finding_id,text=f'诊断策略「{run.snapshot["name"]}」转人工：{reason}')


def available(finding, now):
    return (finding.condition=='active' and finding.state!='resolved' and finding.host_id
            and not finding.host.is_deleted and finding.host.asset_status not in ('maintenance','retired')
            and not (finding.silenced_until and finding.silenced_until>now))


def chosen_tree(cfg, finding):
    if cfg['tree']=='source':
        return diagnosis.recommended_tree(finding,diagnosis.service_probe(finding))
    if cfg['tree']=='custom':
        return DiagnosticTreeRevision.objects.filter(pk=cfg['revision']).first()
    probe = diagnosis.service_probe(finding)
    if probe and probe.unit=='nginx.service':
        return diagnosis.nginx_tree(probe)
    return None


@transaction.atomic
def start(finding_id, policies, now):
    finding = Finding.objects.select_for_update().filter(pk=finding_id).first()
    if finding is None:
        return
    if not available(finding,now) or finding.manual_required:
        return
    if finding.diagnostic_workflows.filter(occurrence=finding.occurrences).exists():
        return
    policy_ids = [policy.pk for policy in policies if matches(policy,finding)]
    matching = [policy for policy in DiagnosticPolicy.objects.filter(enabled=True,pk__in=policy_ids).select_related('actor').order_by('pk')
                if matches(policy,finding)]
    if not matching or not any(policy.config['trigger'] in ('event','both') or not policy.next_scan_at
                               or policy.next_scan_at<=now for policy in matching):
        return
    if finding.diagnostic_runs.filter(task__status__in=ACTIVE).exists():
        return
    policy = matching[0]
    run = DiagnosticWorkflowRun.objects.create(policy=policy,finding=finding,occurrence=finding.occurrences,
                                                snapshot=policy_data(policy))
    if len(matching)>1:
        run.snapshot['fallback'] = any(item.config['fallback'] for item in matching)
        run.save(update_fields=['snapshot'])
        finish(run,'manual','多条策略同时命中。请收窄策略的资产或指标范围，再人工确认本轮告警。')
        return
    try:
        if not policy.actor.is_active or not policy.actor.is_superuser:
            raise PermissionDenied('策略维护人已失去管理员权限')
        with transaction.atomic():
            tree = chosen_tree(run.snapshot,finding)
            previous = finding.diagnostic_runs.filter(
                mode='diagnosis',occurrence=finding.occurrences,task__status='completed',
                task__finished_at__gte=now-timedelta(minutes=15),
            ).select_related('task').last()
            spec = previous.task.options['incident'] if previous else {}
            if (tree and previous and spec.get('tree')==tree.definition and spec.get('tree_version')==tree.version
                    and spec.get('check_id')==getattr(tree,'metadata',{}).get('check_id')
                    and spec.get('executor','ssh')==getattr(tree,'executor','ssh')):
                run.diagnosis = previous
            else:
                run.diagnosis = diagnosis.queue_diagnosis(finding,tree,policy.actor)
    except (ValidationError,PermissionDenied) as exc:
        finish(run,'manual',error_message(exc))
        return
    run.save(update_fields=['diagnosis'])


def error_message(exc):
    return str(exc.detail[0] if isinstance(exc.detail,list) else exc.detail)


def safe_repair_branch(tree, branch, probe):
    if branch not in tree['conclusions']:
        return False
    nodes = {node['id']:node for node in tree['nodes']}

    @lru_cache(maxsize=None)
    def paths(key, http_failed, unit_failed, unknown):
        if key not in nodes:
            return {http_failed and unit_failed and not unknown} if key==branch else set()
        node = nodes[key]
        http = node['kind']=='http' and node['port']==probe.port and node['path']==probe.path
        unit = node['kind']=='systemd' and node['unit']=='nginx.service'
        return set().union(*(paths(node[state],http_failed or (http and state=='failed'),
                                    unit_failed or (unit and state=='failed'),unknown or state=='unknown')
                              for state in diagnosis.OUTCOMES))

    return paths(tree['root'],False,False,False)=={True}


def branch_from(run):
    tree = run.task.options['incident']['tree']
    if not tree['conclusions']:
        return ''
    nodes = {node['id']:node for node in tree['nodes']}
    output = run.task.host_results.values_list('output',flat=True).first() or ''
    rows = re.findall(r'^\[([A-Za-z][A-Za-z0-9_-]{0,29})\] ([a-z]+) · (ok|failed|unknown) · .+$',output,re.M)
    current = tree['root']
    for node_id, kind, state in rows:
        if node_id!=current or current not in nodes or nodes[current]['kind']!=kind:
            return ''
        current = nodes[current][state]
    branches = [line[len('诊断分支：'):] for line in output.splitlines() if line.startswith('诊断分支：')]
    return current if current in tree['conclusions'] and branches==[current] else ''


def validate_workflow(task, finding, probe):
    ref = task.options['incident']['workflow']
    run = DiagnosticWorkflowRun.objects.select_related('policy','diagnosis__task').filter(pk=ref,repair__task=task).first()
    if (not run or run.status!='repairing' or not run.policy.enabled or run.policy.version!=run.snapshot['version']
            or finding.manual_required or not available(finding,timezone.now())
            or run.branch!=run.snapshot['branch'] or not run.diagnosis_id or branch_from(run.diagnosis)!=run.branch
            or not safe_repair_branch(run.diagnosis.task.options['incident']['tree'],run.branch,probe)):
        raise ValidationError('诊断策略版本、审批状态或结论分支已变化，停止修复')
    finished = run.diagnosis.task.finished_at or run.diagnosis.task.created_at
    if finished<timezone.now()-timedelta(minutes=15):
        raise ValidationError('诊断证据已超过 15 分钟，需人工重新核查')


@transaction.atomic
def advance(run_id, now):
    finding_id = DiagnosticWorkflowRun.objects.values_list('finding_id',flat=True).get(pk=run_id)
    finding = Finding.objects.select_for_update().get(pk=finding_id)
    run = DiagnosticWorkflowRun.objects.select_related('policy','diagnosis__task','repair__task').get(pk=run_id)
    if run.status not in OPEN:
        return
    if finding.occurrences!=run.occurrence:
        finish(run,'closed','告警已进入新轮次')
        return
    if run.status=='repairing':
        task = run.repair.task
        if task.status=='awaiting_approval':
            if (not run.policy.enabled or run.policy.version!=run.snapshot['version']
                    or finding.condition=='recovered' or finding.state=='resolved' or finding.manual_required
                    or (run.diagnosis.task.finished_at or run.diagnosis.task.created_at)<now-timedelta(minutes=15)):
                if type(task).objects.filter(pk=task.pk,status='awaiting_approval').update(status='cancelled',finished_at=now):
                    finish(run,'closed' if finding.condition=='recovered' or finding.state=='resolved' else 'manual',
                           '审批已失效，告警、策略或诊断证据已变化')
            else:
                DiagnosticWorkflowRun.objects.filter(pk=run.pk).update(updated_at=now)
            return
        if task.status=='completed':
            if task.options['incident'].get('action') in diagnosis.ssh_baseline.ACTIONS:
                recovered = finding.condition=='recovered'
                finish(run,'recovered' if recovered else 'manual','新 SSH 密钥会话与有效策略复检通过' if recovered else '需人工核对 SSH 复检结果')
            else:
                finish(run,'verifying','本机复检通过，等待外部 HTTP 探测确认')
        elif task.status not in ACTIVE:
            finish(run,'manual' if finding.condition!='recovered' else 'closed',
                   '修复任务未完成，请查看任务结果')
        return
    if run.status=='verifying':
        if finding.condition=='recovered':
            finish(run,'recovered','本机复检通过，外部探测已确认告警恢复')
        elif run.repair.task.finished_at and run.repair.task.finished_at<=now-timedelta(minutes=2):
            finish(run,'manual','本机复检后，外部探测未在 2 分钟内确认恢复')
        else:
            DiagnosticWorkflowRun.objects.filter(pk=run.pk).update(updated_at=now)
        return
    if finding.condition=='recovered':
        finish(run,'closed','告警在诊断期间已恢复')
        return
    if run.diagnosis.task.status in ACTIVE:
        DiagnosticWorkflowRun.objects.filter(pk=run.pk).update(updated_at=now)
        return
    if run.diagnosis.task.status!='completed':
        finish(run,'manual','诊断失败或中断，请查看诊断任务证据')
        return
    run.branch = branch_from(run.diagnosis)
    report = diagnosis.workspace_run(run.diagnosis,run.policy.actor)
    if (not report['checks'] or any(check['state']=='unknown' for check in report['checks'])):
        finish(run,'manual','诊断证据不足，请人工核对')
        return
    cfg = run.snapshot
    if cfg['mode']=='diagnose':
        finish(run,'diagnosed','只读诊断已完成，请查看检查证据')
        return
    baseline = cfg['source']=='inspection' and cfg['signal'] in diagnosis.ssh_baseline.ACTIONS
    if not baseline and run.branch!=cfg['branch']:
        finish(run,'manual','诊断结论未命中已绑定的修复分支')
        return
    try:
        if (not run.policy.enabled or run.policy.version!=cfg['version']
                or not run.policy.actor.is_active or not run.policy.actor.is_superuser
                or finding.manual_required or not available(finding,now)):
            raise ValidationError('策略、告警或资产已变更，已停止本轮自动处理')
        with transaction.atomic():
            if baseline:
                run.repair = diagnosis.ssh_baseline.request(finding,cfg['signal'],run.policy.actor,workflow=run)
                run.branch = 'confirmed_violation'
            else:
                probe = diagnosis.service_probe(finding)
                if not probe or not safe_repair_branch(run.diagnosis.task.options['incident']['tree'],run.branch,probe):
                    raise ValidationError('HTTP 探测已变更，或所选分支未确认同一探测及 Nginx 停止')
                run.repair = diagnosis.queue_repair(finding,probe,run.policy.actor,automatic=cfg['mode']=='auto',
                                                    escalate_on_failure=False,workflow=run.pk,diagnostic_run=run.diagnosis)
    except (ValidationError,PermissionDenied) as exc:
        finish(run,'manual',error_message(exc))
        return
    run.repair_id = run.repair.pk
    run.save(update_fields=['repair'])
    finish(run,'repairing','等待独立管理员审批' if cfg['mode']=='approval' else '已进入受控修复队列')


@transaction.atomic
def repeat_readonly(run_id, now):
    previous = DiagnosticWorkflowRun.objects.select_related('policy__actor').get(pk=run_id)
    finding = Finding.objects.select_for_update().get(pk=previous.finding_id)
    cfg = previous.snapshot
    if (previous.status!='diagnosed' or previous.occurrence!=finding.occurrences
            or not previous.policy.enabled or previous.policy.version!=cfg['version']
            or cfg['mode']!='diagnose' or cfg['trigger']=='event' or not available(finding,now)
            or finding.manual_required or previous.updated_at>now-timedelta(minutes=cfg['interval'])):
        return
    actor = previous.policy.actor
    try:
        if not actor.is_active or not actor.is_superuser:
            raise PermissionDenied('策略维护人已失去管理员权限')
        if finding.diagnostic_runs.filter(task__status__in=ACTIVE).exists():
            return
        with transaction.atomic():
            previous.diagnosis = diagnosis.queue_diagnosis(finding,chosen_tree(cfg,finding),actor)
    except (ValidationError,PermissionDenied) as exc:
        finish(previous,'manual',error_message(exc))
        return
    previous.branch = ''
    previous.save(update_fields=['diagnosis'])
    finish(previous,'diagnosing','仍在触发，已发起定时只读复检')


def dispatch():
    now = timezone.now()
    diagnosis.ssh_baseline.expire_approvals(now)
    terminal_diagnosis = Q(status='diagnosing') & (~Q(diagnosis__task__status__in=ACTIVE) | ~Q(finding__condition='active'))
    terminal_repair = Q(status='repairing') & (~Q(repair__task__status__in=ACTIVE) | Q(repair__task__status='awaiting_approval'))
    due = terminal_diagnosis | terminal_repair | Q(status='verifying')
    for run_id in DiagnosticWorkflowRun.objects.filter(due).order_by('updated_at','pk').values_list('pk',flat=True)[:100]:
        advance(run_id,now)
    policies = list(DiagnosticPolicy.objects.filter(enabled=True).select_related('actor').order_by('pk'))
    for policy in policies:
        cfg = policy.config
        if cfg['mode']!='diagnose' or cfg['trigger']=='event':
            continue
        repeats = DiagnosticWorkflowRun.objects.filter(
            status='diagnosed',policy=policy,snapshot__version=policy.version,
            occurrence=F('finding__occurrences'),finding__condition='active',finding__manual_required=False,
            finding__host__is_deleted=False,updated_at__lte=now-timedelta(minutes=cfg['interval']),
        ).exclude(finding__state='resolved').exclude(finding__host__asset_status__in=['retired','maintenance']).filter(
            Q(finding__silenced_until__isnull=True)|Q(finding__silenced_until__lte=now))
        for run_id in repeats.order_by('updated_at','pk').values_list('pk',flat=True)[:5]:
            repeat_readonly(run_id,now)
    for policy in policies:
        cfg = policy.config
        scheduled = cfg['trigger']=='scheduled'
        if scheduled and policy.next_scan_at and policy.next_scan_at>now:
            continue
        exists = DiagnosticWorkflowRun.objects.filter(finding_id=OuterRef('pk'),occurrence=OuterRef('occurrences'))
        running = Finding.objects.filter(pk=OuterRef('pk'),diagnostic_runs__task__status__in=ACTIVE)
        filters = Q()
        if cfg['signal']:
            if cfg['source']=='service':
                filters = Q(signal__check_id=int(cfg['signal']))
            elif cfg['source']=='inspection':
                filters = Q(signal__check_id=cfg['signal']) | Q(dedupe_key__endswith=':'+cfg['signal'])
            else:
                filters = Q(signal__metric=cfg['signal'])
        candidates = list(Finding.objects.filter(filters,condition='active',manual_required=False,source=cfg['source'],
                                                host_id__in=cfg['host_ids'],host__is_deleted=False)
                          .exclude(state='resolved').exclude(host__asset_status__in=['retired','maintenance'])
                          .filter(Q(silenced_until__isnull=True)|Q(silenced_until__lte=now))
                          .filter(~Exists(exists),~Exists(running)).order_by('pk').values_list('pk',flat=True)[:11])
        for finding_id in candidates[:10]:
            start(finding_id,policies,now)
        if scheduled and len(candidates)<=10:
            DiagnosticPolicy.objects.filter(pk=policy.pk,version=policy.version).update(
                next_scan_at=now+timedelta(minutes=cfg['interval']))


def workflow_data(finding, user):
    run = finding.diagnostic_workflows.filter(occurrence=finding.occurrences).select_related(
        'diagnosis__task','repair__task').first()
    if not run:
        return None
    task = run.repair.task if run.repair_id else None
    return {'policy':run.snapshot['name'],'version':run.snapshot['version'],'mode':run.snapshot['mode'],
            'status':run.status,'reason':run.reason,'branch':run.branch,
            'trigger':run.snapshot['trigger'],'diagnosis_task_id':run.diagnosis.task_id if run.diagnosis_id else None,
            'branch_label':'SSH 策略异常已确认' if run.branch=='confirmed_violation' else (
                run.diagnosis.task.options['incident']['tree']['conclusions'].get(run.branch,'') if run.diagnosis_id else ''),
            'action':diagnosis.ssh_baseline.ACTIONS.get(task.options['incident'].get('action'),diagnosis.ssh_baseline.NGINX) if task else None,
            'task_status':task.status if task else None,'task_id':task.pk if task and user.is_superuser else None,
            'can_approve':bool(task and task.status=='awaiting_approval' and user.is_superuser and task.owner_id!=user.pk)}
