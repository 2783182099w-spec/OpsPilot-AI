"""Managed execution: bounded waves, durable first-batch gate and host evidence."""
from concurrent.futures import ThreadPoolExecutor, wait
from queue import SimpleQueue, Empty
from threading import Event
import shlex
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from host.permissions import require_hosts
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.redaction import redact
from opspilot_api.utils.ssh import SSHParamiko
from .engine import heartbeat
from .managed import UNIT, verify_targets
from .models import ExecutionTask
from .scripts import execute_script, validate_script
from . import ssh_baseline


class Timeline:
    def __init__(self, host_id, events, initial):
        self.host_id, self.events = host_id, events
        self.stages = list(initial)
        self.started = {}
        self.may_have_run = False

    def __call__(self, name, state, detail=''):
        if name=='execute' and state=='running':
            self.may_have_run=True
        existing=next((s for s in self.stages if s['name']==name),None)
        if existing is None:
            existing={'name':name,'started_at':timezone.now().isoformat()}
            self.stages.append(existing)
            self.started[name]=time.monotonic()
        existing.update(status=state,detail=redact(detail)[:500])
        if state!='running':
            existing['finished_at']=timezone.now().isoformat()
            existing['elapsed_ms']=int((time.monotonic()-self.started.get(name,time.monotonic()))*1000)
        self.events.put((self.host_id,{'stages':[dict(x) for x in self.stages],'may_have_run':self.may_have_run}))


def execute_host(host, payload, service, key, stop, events, initial):
    trace=Timeline(host.id,events,initial)
    ssh=None
    code=None
    tail=bytearray()
    output=''
    failure=''
    status='failed'
    phase='connect'

    def capture(chunk):
        tail.extend(chunk)
        del tail[:-14000]
        events.put((host.id,{'output':redact(tail.decode('utf-8',errors='replace'))[:16384]}))

    try:
        if stop.is_set():
            raise InterruptedError('派发前收到取消请求')
        trace(phase,'running')
        ssh=SSHParamiko(host.ip_addr,host.port,host.username,key,connect_timeout=4)
        if ssh.get_connected_client() is None:
            failure=ssh.error_code or 'connection_failed'
            raise ConnectionError(ssh.error_message or 'SSH 连接失败')
        trace(phase,'success')
        phase='script'
        code,output=execute_script(ssh,payload,stop,capture,stage=trace)
        if code!=0:
            if not trace.may_have_run:
                failure='preflight_failed'
            elif code==124:
                status,failure='timeout','remote_timeout_code'
            elif code==137:
                status,failure='interrupted','remote_terminated_code'
            else:
                failure='script_exit'
        elif payload.get('incident',{}).get('action') in ssh_baseline.ACTIONS:
            phase='verify'
            healthy,evidence=ssh_baseline.verify_fresh_session(host,key,payload['incident'],ssh,stop,trace)
            output=output[:13000]+'\n'+evidence[:2048]
            status,failure=('success','') if healthy else ('failed','verification_failed')
        elif service:
            phase='verify'
            trace(phase,'running')
            if stop.is_set():
                raise InterruptedError('脚本已返回，验证前收到取消请求')
            if not UNIT.fullmatch(service):
                raise ValueError('验证服务无效')
            verified,evidence=ssh.execute_cmd('systemctl is-active -- '+shlex.quote(service),
                    timeout=10,stop_event=stop,max_output=2048)
            output=output[:13000]+'\n服务验证 '+service+'：\n'+evidence[:2048]
            healthy=verified==0 and evidence.strip()=='active'
            trace(phase,'success' if healthy else 'failed',evidence)
            status,failure=('success','') if healthy else ('failed','verification_failed')
        else:
            status='success'
    except Exception as exc:
        if phase in ('connect','verify'):
            trace(phase,'failed',str(exc))
        failed=next((x['name'] for x in reversed(trace.stages) if x['status']=='failed'),phase)
        if isinstance(exc,InterruptedError):
            status='interrupted' if trace.may_have_run else 'cancelled'
            failure='remote_unknown' if trace.may_have_run else 'cancelled_before_execution'
        elif isinstance(exc,TimeoutError):
            status='timeout'
            failure='remote_unknown' if failed=='execute' else failed+'_timeout'
        else:
            failure=failure or {'upload':'upload_failed','preflight':'preflight_failed','verify':'verification_failed',
                'execute':'remote_unknown','connect':'connection_failed'}.get(failed,'execution_error')
        output='\n'.join(filter(None,[output[:1000],str(exc)[:1000],tail.decode('utf-8',errors='replace')]))
    finally:
        if ssh:
            ssh.close()
    return {'status':status,'exit_code':code,'output':redact(output)[:16384],
            'stages':trace.stages,'may_have_run':trace.may_have_run,'failure_code':failure}


def run_managed(task):
    managed=task.options['managed']
    owned=ExecutionTask.objects.filter(pk=task.pk,status='running',worker_token=task.worker_token)
    rows=task.host_results
    stop=Event()
    events=SimpleQueue()

    def current():
        if task.worker_token and not heartbeat(task.worker_token):
            return None
        return owned.values('cancel_requested').first()

    def finish(status, reason=''):
        with transaction.atomic():
            state=owned.values('cancel_requested').first()
            if state is None:
                return
            if state['cancel_requested']:
                status,reason='cancelled','用户取消，未派发目标不执行'
            if status=='awaiting_release':
                changed=owned.filter(cancel_requested=False).update(status=status,stop_reason=reason,
                    completed_hosts=rows.exclude(status__in=['queued','running']).count())
                if changed:
                    return
                status,reason='cancelled','用户取消，未派发目标不执行'
            rows.filter(status='queued').update(status='skipped',may_have_run=False,failure_code='not_dispatched',
                output=reason or '未派发',finished_at=timezone.now())
            rows.filter(status='running').update(status='interrupted',may_have_run=None,failure_code='remote_unknown',
                output='执行状态未能确认，核实远端后再处理',finished_at=timezone.now())
            owned.update(status=status,stop_reason=reason,finished_at=timezone.now(),completed_hosts=rows.count())
            incident = task.options.get('incident',{})
            baseline = incident.get('action') in ssh_baseline.ACTIONS
            if status=='completed' and baseline:
                ssh_baseline.mark_recovered(task)
            elif incident.get('escalate_on_failure') and (status in ('failed','interrupted') or (baseline and status=='cancelled')):
                from .diagnostics import escalate_repair
                escalate_repair(task,'修复未完成或未能确认结果，请核对任务证据。')

    try:
        if managed['protected'] and (not task.approved_by_id or task.approved_by_id==task.owner_id):
            finish('failed','缺少独立审批，未派发')
            return
        if task.rollout_phase=='remainder' and not task.released_by_id:
            finish('failed','缺少首批放行记录，未派发')
            return
        if task.options.get('release'):
            from .releases import verify_release
            verify_release(task)
        if task.options.get('incident'):
            from .diagnostics import verify_incident
            verify_incident(task)
        payload=validate_script({**task.options,'source':task.command})
        payload['incident']=task.options.get('incident',{})
        if payload['sha256']!=task.options['sha256']:
            raise ValueError('脚本快照摘要不一致')
        key,_=PkeyManager.get(settings.DEFAULT_KEY_NAME)
        queued=set(rows.filter(status='queued').values_list('host_id_snapshot',flat=True))
        remaining=[i for i in task.host_ids if i in queued]
        if remaining:
            owner=get_user_model().objects.get(pk=task.owner_id)
            verify_targets(task,require_hosts(owner,remaining))
        first=task.rollout_phase=='initial'
        selected=remaining[:managed['canary_count']] if first else remaining
        batch_size=managed['canary_count'] if first else managed['batch_size']
        failures=rows.exclude(status__in=['queued','running','success']).count()
        batch_number=max(rows.values_list('batch',flat=True),default=0)
        with ThreadPoolExecutor(max_workers=payload['concurrency'],thread_name_prefix='opspilot-rollout') as pool:
            for offset in range(0,len(selected),batch_size):
                batch_number+=1
                batch=selected[offset:offset+batch_size]
                for wave_offset in range(0,len(batch),payload['concurrency']):
                    state=current()
                    if state is None:
                        return
                    if state['cancel_requested']:
                        finish('cancelled')
                        return
                    if failures and (first or failures>=managed['failure_limit']):
                        finish('failed','首批未通过，停止派发' if first else '达到失败停止台数，停止派发')
                        return
                    ids=batch[wave_offset:wave_offset+payload['concurrency']]
                    owner=get_user_model().objects.get(pk=task.owner_id)
                    hosts={h.id:h for h in require_hosts(owner,ids)}
                    verify_targets(task,hosts.values())
                    if task.options.get('release'):
                        verify_release(task)
                    if task.options.get('incident'):
                        verify_incident(task)
                    active={}
                    for ident in ids:
                        state=current()
                        if state is None or state['cancel_requested']:
                            stop.set()
                            break
                        now=timezone.now()
                        initial=[{'name':'queue','status':'success','started_at':task.created_at.isoformat(),
                                  'finished_at':now.isoformat(),'elapsed_ms':max(0,int((now-task.created_at).total_seconds()*1000)),
                                  'detail':'从提交到本机派发，包含等待审批或放行'}]
                        rows.filter(host_id_snapshot=ident,status='queued').update(status='running',
                            started_at=now,batch=batch_number,stages=initial)
                        active[pool.submit(execute_host,hosts[ident],payload,managed['verify_service'],key,stop,events,initial)]=ident
                    while active:
                        wait(active,timeout=0.5,return_when='FIRST_COMPLETED')
                        state=current()
                        if state is None:
                            stop.set()
                            return
                        if state['cancel_requested']:
                            stop.set()
                        snapshots={}
                        while True:
                            try:
                                ident,patch=events.get_nowait()
                            except Empty:
                                break
                            snapshots.setdefault(ident,{}).update(patch)
                        for ident,patch in snapshots.items():
                            rows.filter(host_id_snapshot=ident,status='running').update(**patch)
                        for future in [f for f in active if f.done()]:
                            ident=active.pop(future)
                            result=future.result()
                            rows.filter(host_id_snapshot=ident,status='running').update(**result,finished_at=timezone.now())
                            failures+=result['status']!='success'
                        owned.update(completed_hosts=rows.exclude(status__in=['queued','running']).count())
                    if stop.is_set():
                        finish('cancelled')
                        return
        if failures and (first or failures>=managed['failure_limit']):
            finish('failed','首批未通过，停止派发' if first else '达到失败停止台数，停止派发')
        elif first and rows.filter(status='queued').exists():
            finish('awaiting_release','首批通过，等待人工核对并放行')
        else:
            finish('failed' if failures else 'completed','有主机未通过' if failures else '')
    except Exception as exc:
        stop.set()
        finish('failed',redact(str(exc))[:300] or '任务准备失败')
