"""Remote checks used by diagnosis and the built-in Nginx recovery action."""
import http.client
import json
import os
import shutil
import socket
import subprocess
import time


def systemctl(*args):
    return subprocess.run(['systemctl', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, errors='replace', timeout=5)


def http_status(port, path):
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        client.request('GET', path)
        with client.getresponse() as response:
            return response.status
    finally:
        client.close()


def evaluate(node):
    kind = node['kind']
    if kind == 'systemd':
        result = systemctl('is-active', '--', node['unit'])
        value = result.stdout.strip()
        if value in ('active','failed','inactive'):
            return 'ok' if value == 'active' else 'failed', value
        return 'unknown', value or '无法读取单元状态'
    if kind == 'http':
        status = http_status(node['port'], node['path'])
        return 'ok' if 200 <= status < 300 else 'failed', f'HTTP {status}'
    if kind == 'port':
        with socket.create_connection(('127.0.0.1', node['port']), timeout=3):
            return 'ok', '本机 TCP 端口可达'
    if kind == 'resource':
        metric = node['metric']
        if metric in ('disk','inode') and node.get('all_mounts'):
            return filesystem_evidence(metric,node['threshold'])
        value = resource(metric)
        return ('failed' if value > node['threshold'] else 'ok',
                f'{metric} {value:.1f} / 告警阈值 {node["threshold"]:g}')
    if kind == 'resource_context':
        return 'ok', resource_context(node['metric'])
    if kind == 'snapshot':
        return 'ok', f'1 分钟负载 {resource("load"):.2f} · 内存 {resource("memory"):.1f}% · 根分区 {resource("disk"):.1f}%'
    if kind == 'disk':
        total, used, _ = shutil.disk_usage(node['path'])
        percent = 100 * used / total
        return 'ok' if percent < node['threshold'] else 'failed', f'已用 {percent:.1f}%'
    result = subprocess.run(['journalctl', '--no-pager', '-q', '-u', node['unit'], '-n', '100',
                             '--since', '-15min', '-o', 'cat'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                            errors='replace', timeout=5)
    if result.returncode or result.stderr.strip() or not result.stdout.strip():
        return 'unknown', '近 15 分钟无可读取的日志'
    count = sum(node['keyword'].casefold() in line.casefold() for line in result.stdout.splitlines())
    return 'failed' if count else 'ok', f'最近最多 100 行日志中匹配 {count} 行'


def filesystem_evidence(metric, threshold):
    columns = 'ipcent,target' if metric=='inode' else 'pcent,target'
    flags = ['-x'+kind for kind in ('tmpfs','devtmpfs','overlay','squashfs','proc','sysfs')]
    response = subprocess.run(['df','--output='+columns,*flags],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              universal_newlines=True,errors='replace',env={**os.environ,'LC_ALL':'C'},timeout=8)
    mounts = []
    for line in response.stdout.splitlines()[1:]:
        fields = line.split(maxsplit=1)
        if len(fields)==2 and fields[0].endswith('%') and fields[0][:-1].isdigit():
            mounts.append((int(fields[0][:-1]),fields[1]))
    if not mounts:
        return 'unknown','未取得有效挂载点数据'
    value, mount = max(mounts)
    state = 'failed' if value>threshold else 'unknown' if response.returncode else 'ok'
    return state, f'{metric} {value} / 告警阈值 {threshold:g} · 最高挂载点 {mount[:140]}'


def resource_context(metric):
    if metric=='load':
        with open('/proc/loadavg',encoding='ascii') as source:
            queue = source.read().split()[3]
        cores = os.cpu_count() or 0
        return f'逻辑 CPU {cores} · 当前可运行进程/总进程 {queue}'
    column = 'pcpu' if metric=='cpu' else 'rss'
    output = subprocess.run(['ps','-eo','pid,'+column+',comm','--sort=-'+column],
                            stdout=subprocess.PIPE,check=True,universal_newlines=True,errors='replace',timeout=5)
    rows = [' '.join(line.split())[:55] for line in output.stdout.splitlines()[1:4]]
    if not rows:
        raise ValueError('No processes')
    units = 'CPU 历史平均%' if metric=='cpu' else 'RSS KiB'
    return f'PID / {units} / 进程名 · '+' | '.join(rows)


def resource(metric):
    if metric == 'load':
        return os.getloadavg()[0]
    if metric == 'inode':
        stat = os.statvfs('/')
        return 100 * (stat.f_files - stat.f_ffree) / stat.f_files
    if metric == 'disk':
        total, used, _ = shutil.disk_usage('/')
        return 100 * used / total
    if metric == 'memory':
        with open('/proc/meminfo', encoding='ascii') as handle:
            fields = dict(line.split(':', 1) for line in handle if ':' in line)
        return 100 * (1 - int(fields['MemAvailable'].split()[0]) / int(fields['MemTotal'].split()[0]))
    def sample_cpu():
        with open('/proc/stat', encoding='ascii') as handle:
            values = [int(value) for value in handle.readline().split()[1:9]]
        return sum(values), values[3] + values[4]
    total, idle = sample_cpu()
    time.sleep(1)
    next_total, next_idle = sample_cpu()
    return 100 * (1 - (next_idle - idle) / (next_total - total))


def run_tree(tree):
    nodes = {node['id']:node for node in tree['nodes']}
    current = tree['root']
    while current in nodes:
        node = nodes[current]
        try:
            state, evidence = evaluate(node)
        except ConnectionRefusedError:
            state, evidence = 'failed', '本机服务拒绝连接'
        except (OSError, ValueError, ArithmeticError, KeyError, subprocess.SubprocessError, http.client.HTTPException) as exc:
            state, evidence = 'unknown', type(exc).__name__
        print(f'[{current}] {node["kind"]} · {state} · {evidence}', flush=True)
        current = node[state]
    print(f'诊断分支：{current}', flush=True)
    print(f'诊断结论：{tree["conclusions"][current]}', flush=True)


def heal(config):
    unit = 'nginx.service'
    try:
        status = http_status(config['port'], config['path'])
    except ConnectionRefusedError:
        pass
    else:
        print(f'本机探测已得到 HTTP {status}，停止自动启动，需核对代理和后端', flush=True)
        return 3

    fields = systemctl('show', '--property=ActiveState,Result,LoadState', '--', unit)
    properties = dict(line.split('=', 1) for line in fields.stdout.splitlines() if '=' in line)
    if fields.returncode or properties != {'ActiveState':'failed','Result':'exit-code','LoadState':'loaded'}:
        print('服务状态不符合自动修复条件，转人工', flush=True)
        return 3
    checked = subprocess.run(['nginx','-t'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    if checked.returncode:
        print('Nginx 配置验证失败，转人工', flush=True)
        return 3

    print('配置验证通过，尝试启动一次 nginx.service', flush=True)
    started = systemctl('start', '--', unit)
    if started.returncode:
        print('启动失败，转人工', flush=True)
        return 4
    for attempt in range(3):
        status = http_status(config['port'], config['path'])
        if not 200 <= status < 300:
            print(f'复检 HTTP {status}，转人工', flush=True)
            return 4
        print(f'本机 HTTP 复检 {attempt + 1}/3 通过', flush=True)
        if attempt < 2:
            time.sleep(2)
    return 0
