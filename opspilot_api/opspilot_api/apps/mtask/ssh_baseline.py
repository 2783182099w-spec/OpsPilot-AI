"""Approval gate and worker coordination for privileged SSH baseline changes."""
from datetime import timedelta
from pathlib import Path
import re
import secrets
import shlex

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from host.models import Finding, FindingNote, Host
from opspilot_api.utils.ssh import SSHParamiko
from .diagnostic_routes import source_plan
from .managed import ACTIVE
from .models import DiagnosticRun, ExecutionTask

SCRIPT = Path(__file__).with_name('ssh_baseline_script.py')
ACTIONS = {
    'ssh_password': {
        'name':'禁用 SSH 密码登录',
        'impact':'关闭密码和键盘交互认证。保留密钥登录，复连验证失败会触发回退。',
    },
    'ssh_root': {
        'name':'禁止 root 使用密码登录',
        'impact':'将 root 登录限制为密钥等非密码方式。保留平台的 root 密钥通道，复连验证失败会触发回退。',
    },
}
NGINX = {'name':'校验配置后启动 Nginx','impact':'仅启动一次，然后做本机检查和外部 HTTP 复检。'}


def action_for(finding):
    if finding.source!='inspection':
        return ''
    plan = source_plan(finding)
    check = plan.metadata.get('check_id') if plan else None
    return check if check in ACTIONS else ''


def script_source(action, token):
    return SCRIPT.read_text(encoding='utf-8')+f'\nraise SystemExit(main({action!r},{token!r},*sys.argv[1:]))\n'


def evidence(finding, action, run_id=None):
    cutoff = timezone.now()-timedelta(minutes=15)
    rows = finding.diagnostic_runs.filter(mode='diagnosis',occurrence=finding.occurrences,
                                          task__status='completed',task__finished_at__gte=cutoff)
    if run_id:
        rows = rows.filter(pk=run_id)
    run = rows.select_related('task').last()
    if not run or run.task.options['incident'].get('check_id')!=action:
        raise ValidationError('请先完成本轮 SSH 专项诊断，证据有效期为 15 分钟。')
    result = run.task.host_results.filter(host_id_snapshot=finding.host_id,status='success').first()
    expected = 'passwordauthentication yes' if action=='ssh_password' else 'permitrootlogin yes'
    if not result or not any(check.get('id')==action and check.get('state')=='warning'
                             and check.get('evidence','').strip().lower()==expected for check in result.checks):
        raise ValidationError('最新诊断未确认目标 SSH 策略异常，建议人工核对或重新诊断。')
    return run


def check(finding, action, run_id=None):
    host = finding.host
    if action_for(finding)!=action or finding.condition=='recovered' or finding.state=='resolved':
        raise ValidationError('告警状态或基线检查项已变化。')
    if (not host or host.is_deleted or host.environment!='test' or host.username!='root'
            or host.asset_status in ('maintenance','retired')):
        raise ValidationError('SSH 修复仅开放给使用 root 密钥纳管、且处于可操作状态的测试资产。')
    if finding.silenced_until and finding.silenced_until>timezone.now():
        raise ValidationError('告警已静默，暂停 SSH 修复。')
    return evidence(finding,action,run_id)


def check_request(finding, action, run_id=None):
    run = check(finding,action,run_id)
    repairs = DiagnosticRun.objects.filter(mode='repair',finding__host_id=finding.host_id)
    dispatched = repairs.filter(task__started_at__isnull=False)
    if (repairs.filter(task__status__in=ACTIVE).exists()
            or dispatched.filter(task__started_at__gte=timezone.now()-timedelta(minutes=30)).exists()
            or dispatched.filter(finding=finding,occurrence=finding.occurrences).exists()):
        raise ValidationError('该主机已有在途或近期修复，或本轮已提交过修复。')
    return run


def request(finding, action, actor, workflow=None):
    from .diagnostics import create_incident

    Host.objects.select_for_update().only('pk').get(pk=finding.host_id)
    run = check_request(finding,action,workflow.diagnosis_id if workflow else None)
    token = secrets.token_hex(16)
    return create_incident(finding,actor,script_source(action,token),'repair',True,{
        'action':action,'rollback_token':token,'diagnosis_id':run.pk,'diagnosis_task_id':run.task_id,
        'tree_name':run.task.options['incident']['tree_name'],'automatic':False,
        'workflow':workflow.pk if workflow else None,
        'automation_scope':'approval','escalate_on_failure':not workflow,
    })


def expire_approvals(now):
    expired = ExecutionTask.objects.filter(kind='inspection',status='completed',
        finished_at__lt=now-timedelta(minutes=15)).values('pk')
    pending = ExecutionTask.objects.filter(status='awaiting_approval',options__incident__action__in=list(ACTIONS),
        options__incident__workflow=None).order_by('pk')
    for task in pending[:100]:
        if not expired.filter(pk=task.options['incident']['diagnosis_task_id']).exists():
            continue
        with transaction.atomic():
            changed = ExecutionTask.objects.filter(pk=task.pk,status='awaiting_approval').update(
                status='cancelled',finished_at=now,completed_hosts=1,
                stop_reason='SSH 诊断证据已过期，未派发，请重新诊断后提交',
            )
            if changed:
                task.host_results.filter(status='queued').update(
                    status='skipped',may_have_run=False,failure_code='not_dispatched',finished_at=now,
                    output='审批已过期，未派发',
                )
                FindingNote.objects.create(finding_id=task.options['incident']['finding_id'],
                    text=f'任务 #{task.pk} 的 SSH 诊断证据已过期，可重新诊断后提交。')


def verify(task, finding):
    meta = task.options['incident']
    action, token = meta.get('action'),meta.get('rollback_token')
    if (not finding or action not in ACTIONS or not re.fullmatch('[a-f0-9]{32}',token or '')
            or not task.options['managed']['protected'] or meta.get('automatic')
            or task.command!=script_source(action,token)):
        raise ValidationError('SSH 修复动作快照无效，请重新提交')
    if task.status!='awaiting_approval' and (not task.approved_by_id or not task.approved_by.is_active
                                             or not task.approved_by.is_superuser or task.approved_by_id==task.owner_id):
        raise ValidationError('SSH 变更缺少仍有效的独立管理员审批')
    check(finding,action,meta['diagnosis_id'])
    if meta.get('workflow'):
        from .models import DiagnosticWorkflowRun
        workflow = DiagnosticWorkflowRun.objects.select_related('policy').filter(
            pk=meta['workflow'],repair__task=task,diagnosis_id=meta['diagnosis_id'],status='repairing',
            branch='confirmed_violation',
        ).first()
        if (not workflow or not workflow.policy.enabled or workflow.policy.version!=workflow.snapshot['version']
                or workflow.snapshot['mode']!='approval' or workflow.snapshot['signal']!=action):
            raise ValidationError('SSH 修复策略已变更，停止执行')


def verify_fresh_session(host, key, meta, original, stop, trace):
    """The on-host timer is armed until a fresh trusted session commits the change."""
    trace('verify','running','正在建立新的密钥连接')
    fresh = SSHParamiko(host.ip_addr,host.port,host.username,key,connect_timeout=4)
    script = shlex.quote('/var/lib/opspilot-sshd/'+meta['rollback_token']+'/change.py')
    command = '/usr/bin/env python3 -u -- '+script
    try:
        if stop.is_set() or fresh.get_connected_client() is None or stop.is_set():
            raise RuntimeError('未能通过新密钥连接复检')
        code, output = fresh.execute_cmd(command+' commit',timeout=15,max_output=2048)
        if code or '已确认 SSH 基线变更' not in output:
            raise RuntimeError(output or '未确认 SSH 配置提交')
        trace('verify','success',output)
        return True, output
    except Exception:
        try:
            code, output = original.execute_cmd(command+' rollback',timeout=15,max_output=2048)
            if not code and 'SSH 基线变更已回退' in output:
                trace('verify','failed','新密钥连接复检失败，主机已回退')
                return False, output
        except Exception:
            pass
        message = '未能确认复检或回退结果，请核对主机回滚计时器与任务证据。'
        trace('verify','failed',message)
        return False, message
    finally:
        fresh.close()


def mark_recovered(task):
    meta = task.options['incident']
    finding = Finding.objects.select_for_update().filter(pk=meta['finding_id'],occurrences=meta['occurrence']).first()
    if finding and finding.condition!='recovered':
        Finding.objects.filter(pk=finding.pk).update(condition='recovered',recovered_at=timezone.now())
        FindingNote.objects.create(finding=finding,text=f'任务 #{task.pk} 已通过新 SSH 密钥连接与生效策略复检。')
