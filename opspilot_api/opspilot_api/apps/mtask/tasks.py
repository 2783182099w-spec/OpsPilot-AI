from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from queue import Queue, Empty, Full
from threading import Event
import socket
import time

from django.conf import settings
from django.utils import timezone
from host.models import Host, Finding
from host.permissions import require_hosts
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.redaction import redact
from opspilot_api.utils.ssh import SSHParamiko
from .models import ExecutionTask, HostResult
from .inspection import command as inspection_command, parse, validate_options
from .engine import heartbeat
from .scripts import validate_script,execute_script


def record_findings(task, host_id, checks):
    for check in checks:
        key = f'{task.owner_id}:{host_id}:{check["id"]}'
        if check['state'] in ('warning', 'unknown', 'review'):
            issue, created = Finding.objects.get_or_create(dedupe_key=key, defaults={'owner': task.owner, 'host_id': host_id, 'title': check['title']})
            issue.signal = {'check_id':check['id']}
            issue.evidence = redact(check.get('evidence', '') + '\n' + check['summary'])[:6000]
            issue.severity = 'warning' if check['state'] == 'warning' else 'info'
            if issue.state == 'resolved':
                issue.state = 'open'
            if not created and issue.condition == 'recovered':
                issue.occurrences += 1
            issue.condition = 'unknown' if check['state'] in ('unknown','review') else 'active'
            issue.recovered_at = None
            issue.source_task = task
            issue.save()
        elif check['state'] == 'pass':
            Finding.objects.filter(dedupe_key=key).exclude(state='resolved').update(state='resolved', condition='recovered', recovered_at=timezone.now(), last_seen=timezone.now())


def run_task(task):
    if task.options.get('managed'):
        from .rollout import run_managed
        return run_managed(task)
    stop = Event()
    updates = Queue(maxsize=64)
    previews = {}
    try:
        hosts = require_hosts(task.owner, task.host_ids)
        if task.options.get('release_precheck'):
            from .release_config import verify_precheck
            verify_precheck(task,hosts)
        options = validate_options(task.kind, task.options) if task.kind in ('inspection', 'probe') else task.options
        incident = task.options.get('incident',{})
        if incident.get('executor') == 'inspection':
            options['focus_check'] = incident['check_id']
        if task.kind=='script':
            options = validate_script({**task.options,'source':task.command})
        concurrency = options.get('concurrency',4) if task.kind=='script' else 4
        private_key = None
        if task.kind != 'probe':
            private_key, _ = PkeyManager.get(settings.DEFAULT_KEY_NAME)
        HostResult.objects.bulk_create([HostResult(task=task, host_id_snapshot=host.id, host_info={'id': host.id, 'name': host.name, 'ip_addr': host.ip_addr, 'port': host.port}) for host in hosts], ignore_conflicts=True)

        def execute(host):
            ssh = None
            live_tail = bytearray()
            result = {'status': 'failed', 'exit_code': None, 'output': '', 'checks': [], 'state': 'unknown'}
            if stop.is_set():
                return {**result, 'status': 'cancelled', 'output': '尚未连接，任务已取消'}
            try:
                if task.kind == 'probe' and incident.get('executor')=='monitor':
                    from .diagnostic_routes import check_monitoring
                    metric = incident['metric']
                    state,evidence = check_monitoring(host,metric)
                    title = 'Node Exporter 采集器' if metric=='collector_failures' else 'Prometheus 采集链路'
                    result.update(status='success',exit_code=0,output=evidence,checks=[{
                        'id':'check','title':title,'state':{'ok':'pass','failed':'warning','unknown':'unknown'}[state],
                        'summary':evidence,'evidence':'',
                    }])
                    return result
                if task.kind == 'probe':
                    started = time.monotonic()
                    with socket.create_connection((host.ip_addr, host.port), timeout=3):
                        pass
                    result.update(status='success', exit_code=0, state='reachable', output=f'SSH TCP 端口可达，耗时 {int((time.monotonic()-started)*1000)} ms；未验证登录或服务健康')
                    result['checks'] = [{'id': 'connectivity', 'title': 'SSH 端口可达性', 'state': 'pass', 'evidence': '', 'summary': result['output']}]
                    return result
                ssh = SSHParamiko(host.ip_addr, host.port, host.username, private_key, connect_timeout=4)
                if ssh.get_connected_client() is None:
                    raise ConnectionError(ssh.error_message or 'SSH 登录或指纹验证失败')
                if stop.is_set():
                    return {**result, 'status': 'cancelled', 'output': '命令尚未发送，任务已取消'}
                cmd = inspection_command(options) if task.kind == 'inspection' else task.command
                def output(chunk):
                    live_tail.extend(chunk)
                    if len(live_tail)>14000:
                        del live_tail[:-14000]
                    try:
                        updates.put_nowait((host.id, chunk))
                    except Full:
                        pass
                if task.kind=='script':
                    code, output_text = execute_script(ssh,options,stop,output)
                else:
                    code, output_text = ssh.execute_cmd(cmd, timeout=60, stop_event=stop, on_output=output, max_output=131072 if task.kind == 'inspection' else 65536)
                result.update(status='success' if code == 0 else 'failed', exit_code=code, state='reachable', output=redact(output_text)[:16384])
                if task.kind=='script' and code in (124,137):
                    result['status'] = 'timeout' if code==124 else 'interrupted'
                    result['output'] += '\n远端 timeout 返回 124，或进程被强制终止（137）；请结合脚本及主机状态判断，不能保证所有子进程均已结束。'
                if task.kind == 'inspection':
                    result['checks'] = parse(output_text, options)
            except InterruptedError as exc:
                result.update(status='interrupted', output=redact(str(exc)+'\n'+live_tail.decode('utf-8',errors='replace')))
            except TimeoutError as exc:
                result.update(status='timeout', output=redact(str(exc)+'\n'+live_tail.decode('utf-8',errors='replace')))
                if task.kind == 'probe':
                    result['state'] = 'unreachable'
                    if incident.get('executor')=='tcp':
                        result.update(status='success',exit_code=0)
                result['checks'] = [{'id':'connectivity','title':'连接或只读检查超时','state':'unknown','evidence':'','summary':result['output']}]
            except Exception as exc:
                result['output'] = redact(str(exc))[:1000]
                if live_tail:
                    result['output'] += '\n最后输出（可能不完整）：\n'+redact(live_tail.decode('utf-8',errors='replace'))
                if task.kind == 'probe':
                    result['state'] = 'unreachable'
                    if incident.get('executor')=='tcp':
                        result.update(status='success',exit_code=0)
                result['checks'] = [{'id': 'connectivity', 'title': 'SSH 端口不可达' if task.kind == 'probe' else 'SSH 连接或认证失败', 'state': 'warning', 'evidence': '', 'summary': result['output']}]
            finally:
                if ssh:
                    ssh.close()
            return result

        completed = 0
        if ExecutionTask.objects.filter(pk=task.pk, cancel_requested=True).exists():
            stop.set()
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='opspilot-ssh') as pool:
            remaining = iter(hosts)
            active = {}
            def fill():
                while len(active) < concurrency and not stop.is_set():
                    host = next(remaining, None)
                    if host is None:
                        break
                    HostResult.objects.filter(task=task, host_id_snapshot=host.id).update(status='running', started_at=timezone.now())
                    active[pool.submit(execute, host)] = host
            fill()
            while active:
                current = ExecutionTask.objects.values('cancel_requested').get(pk=task.pk)
                if current['cancel_requested'] or (task.worker_token and not heartbeat(task.worker_token)):
                    stop.set()
                finished, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                while True:
                    try:
                        host_id, chunk = updates.get_nowait()
                        previews[host_id] = (previews.get(host_id, b'') + chunk)[-65536:]
                    except Empty:
                        break
                for host_id, preview in previews.items():
                    HostResult.objects.filter(task=task, host_id_snapshot=host_id, status='running').update(output=redact(preview.decode('utf-8', errors='replace'))[-16384:])
                for future in finished:
                    host = active.pop(future)
                    item = future.result()
                    HostResult.objects.filter(task=task, host_id_snapshot=host.id).update(status=item['status'], exit_code=item['exit_code'], output=item['output'], checks=item['checks'], finished_at=timezone.now())
                    if task.kind in ('probe', 'inspection') and not incident:
                        Host.objects.filter(pk=host.id).update(connection_state=item['state'], last_checked_at=timezone.now(), last_check_note=item['output'][:300] if task.kind == 'probe' or item['status'] != 'success' else 'SSH 已连接并完成只读检查')
                        record_findings(task, host.id, item['checks'])
                        if task.kind == 'inspection':
                            os_check = next((check for check in item['checks'] if check['id'] == 'os'), None)
                            if os_check and os_check['evidence']:
                                import re
                                match = re.search(r'PRETTY_NAME="?([^\n"]+)', os_check['evidence'])
                                if match:
                                    Host.objects.filter(pk=host.id).update(os_name=match.group(1)[:150])
                    previews.pop(host.id, None)
                    completed += 1
                    ExecutionTask.objects.filter(pk=task.pk).update(completed_hosts=completed)
                fill()
        if stop.is_set():
            HostResult.objects.filter(task=task, status='queued').update(status='cancelled', output='未执行：已取消', finished_at=timezone.now())
        states = set(HostResult.objects.filter(task=task).values_list('status', flat=True))
        status = 'cancelled' if stop.is_set() else 'completed' if states == {'success'} else 'failed'
        ExecutionTask.objects.filter(pk=task.pk).update(status=status, finished_at=timezone.now(), completed_hosts=HostResult.objects.filter(task=task).exclude(status__in=['queued','running']).count())
    except Exception as exc:
        HostResult.objects.filter(task=task, status__in=['queued','running']).update(status='interrupted', output=redact(str(exc))[:1000], finished_at=timezone.now())
        ExecutionTask.objects.filter(pk=task.pk).update(status='failed', results=[{'error': redact(str(exc))[:1000]}], finished_at=timezone.now())
