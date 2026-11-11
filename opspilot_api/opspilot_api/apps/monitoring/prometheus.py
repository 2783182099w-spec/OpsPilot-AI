"""Fixed, permission-scoped PromQL. No arbitrary PromQL/URLs from browsers."""
import json
import math
import time
import requests
from django.conf import settings
from django.core.cache import cache


class Unavailable(Exception):
    pass


def api(path, params=None, post=False):
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.request('POST' if post else 'GET', settings.PROMETHEUS_URL.rstrip('/') + path, params=params, timeout=(2, 8), stream=True, allow_redirects=False)
            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                raise Unavailable('Prometheus 未返回有效响应')
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 8*1024*1024:
                    raise Unavailable('Prometheus 响应超过大小限制')
            if post:
                return {}
            value = json.loads(body)
            if value.get('status') != 'success':
                raise Unavailable('Prometheus 查询失败')
            return value['data']
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise Unavailable('Prometheus 暂不可用，请检查服务、隧道及采集配置') from exc


METRICS = {'up':'采集可用性', 'cpu':'CPU 使用率', 'memory':'内存使用率', 'disk':'最高文件系统使用率', 'inode':'最高 inode 使用率', 'load':'一分钟负载', 'rx':'网络接收速率', 'tx':'网络发送速率', 'uptime':'启动时长', 'sample_time':'最近采集时间', 'collector_failures':'失败采集器数量'}


def expressions(ids=None):
    matcher = 'job="opspilot_nodes"'
    if ids is not None:
        matcher += ',host_id=~"(' + '|'.join(str(int(i)) for i in ids) + ')"'
    def s(name, extra=''):
        return name + '{' + matcher + extra + '}'
    fs = ',fstype!~"tmpfs|devtmpfs|overlay|squashfs|proc|sysfs"'
    return {
        'collector_failures': 'sum by(host_id) (1 - '+s('node_scrape_collector_success')+')',
        'up': 'max by(host_id) ('+s('up')+')',
        'sample_time': 'max by(host_id) (timestamp('+s('up')+'))',
        'cpu': '100 * (1 - avg by(host_id) (rate('+s('node_cpu_seconds_total', ',mode="idle"')+'[2m])))',
        'memory': '100 * (1 - max by(host_id) ('+s('node_memory_MemAvailable_bytes')+') / max by(host_id) ('+s('node_memory_MemTotal_bytes')+'))',
        'disk': 'max by(host_id) (100 * (1 - '+s('node_filesystem_avail_bytes',fs)+' / '+s('node_filesystem_size_bytes',fs)+'))',
        'inode': 'max by(host_id) (100 * (1 - '+s('node_filesystem_files_free',fs)+' / '+s('node_filesystem_files',fs)+'))',
        'load': 'max by(host_id) ('+s('node_load1')+')',
        'rx': 'sum by(host_id) (rate('+s('node_network_receive_bytes_total', ',device!="lo"')+'[2m]))',
        'tx': 'sum by(host_id) (rate('+s('node_network_transmit_bytes_total', ',device!="lo"')+'[2m]))',
        'uptime': 'time() - max by(host_id) ('+s('node_boot_time_seconds')+')',
    }


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def latest(ids):
    ids = sorted(set(ids))
    if not ids:
        return {}
    key = 'monitor-latest:' + ','.join(map(str, ids))
    cached = cache.get(key)
    if cached is not None:
        return cached
    expr = ' or '.join('label_replace(('+value+'), "opspilot_metric", "'+name+'", "", "")' for name,value in expressions(ids).items())
    rows = api('/api/v1/query', {'query': expr, 'timeout':'6s'}).get('result', [])
    data = {i:{} for i in ids}
    for row in rows:
        labels = row.get('metric', {})
        try:
            ident = int(labels.get('host_id', ''))
        except ValueError:
            continue
        if ident in data and labels.get('opspilot_metric') in METRICS:
            data[ident][labels['opspilot_metric']] = finite(row['value'][1])
    cache.set(key, data, 10)
    return data


def history(host_id, metric, hours):
    end = time.time()
    start = end-hours*3600
    step = max(60, math.ceil((end-start)/360))
    rows = api('/api/v1/query_range', {'query':expressions([host_id])[metric], 'start':start,'end':end,'step':step,'timeout':'6s'}).get('result', [])
    return [[float(t), finite(v)] for row in rows[:1] for t,v in row.get('values', [])][:361]


def fleet_history(ids):
    if not ids:
        return {'cpu':[],'memory':[]}
    import hashlib
    key='fleet-history:'+hashlib.sha256(','.join(map(str,sorted(ids))).encode()).hexdigest()
    existing=cache.get(key)
    if existing is not None:
        return existing
    expr=expressions(ids)
    query=' or '.join('label_replace(avg(('+expr[name]+') and on(host_id) ('+expr['up']+' == 1)), "opspilot_metric", "'+name+'", "", "")' for name in ('cpu','memory'))
    end=time.time()
    rows=api('/api/v1/query_range',{'query':query,'start':end-3600,'end':end,'step':60,'timeout':'6s'}).get('result',[])
    data={'cpu':[],'memory':[]}
    for row in rows:
        name=row.get('metric',{}).get('opspilot_metric')
        if name in data:
            data[name]=[[float(t),finite(v)] for t,v in row.get('values',[])][:61]
    cache.set(key,data,30)
    return data
