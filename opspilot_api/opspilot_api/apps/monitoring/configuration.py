import json
import os
import tempfile
import re
import ipaddress
import time
from pathlib import Path
from django.conf import settings
from rest_framework.exceptions import ValidationError
from .models import MonitorTarget, MonitorRule
from .prometheus import expressions, api


def validate_endpoint(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9.-]+:[0-9]{1,5}', value):
        raise ValidationError('采集地址须为 IPv4/域名:端口，不含协议、路径或凭据')
    host, port = value.rsplit(':',1)
    if not 1 <= int(port) <= 65535:
        raise ValidationError('采集端口无效')
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValidationError('不允许未指定、组播或链路本地采集地址')
    except ValueError:
        if host.endswith('.local') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]',host):
            raise ValidationError('采集域名无效')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.opspilot-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
        # Prometheus may briefly hold discovery files without FILE_SHARE_DELETE
        # on Windows. Preserve the old valid file and retry for a bounded time.
        for attempt in range(6):
            try:
                os.replace(temp,path)
                break
            except PermissionError:
                if attempt==5:
                    raise
                time.sleep(min(0.05*(2**attempt),0.8))
    finally:
        Path(temp).unlink(missing_ok=True)


def write_config():
    root = Path(settings.MONITOR_CONFIG_DIR)
    targets = [{'targets':[t.endpoint], 'labels':{'host_id':str(t.host_id)}} for t in MonitorTarget.objects.filter(enabled=True,host__is_deleted=False)]
    expr = expressions()
    rules = []
    for row in MonitorRule.objects.filter(enabled=True):
        expression = '('+expr[row.metric]+') ' + ('< 1' if row.metric=='up' else '> '+str(row.threshold))
        if row.metric != 'up':
            expression += ' and on(host_id) ('+expr['up']+' == 1)'
        rules.append({'alert':'OpsPilotRule'+str(row.id),'expr':expression,'for':str(row.duration)+'s','keep_firing_for':str(row.recovery_seconds)+'s','labels':{'source':'opspilot','rule_id':str(row.id),'severity':row.severity},'annotations':{'summary':row.name}})
    atomic_json(root/'targets.json', targets)
    atomic_json(root/'rules.yml', {'groups':[{'name':'opspilot','interval':'30s','rules':rules}]})
    config = {'global':{'scrape_interval':'60s','scrape_timeout':'10s','evaluation_interval':'30s'},'rule_files':[str((root/'rules.yml').resolve()).replace('\\','/')], 'scrape_configs':[{'job_name':'opspilot_nodes','sample_limit':10000,'file_sd_configs':[{'files':[str((root/'targets.json').resolve()).replace('\\','/')],'refresh_interval':'15s'}]}]}
    atomic_json(root/'prometheus.yml',config)
    return len(targets), len(rules)


def defaults():
    if MonitorRule.objects.exists():
        return
    for name,metric,threshold,duration,severity in [('采集端点失联','up',1,120,'critical'),('CPU 持续高使用率','cpu',90,300,'warning'),('内存持续高使用率','memory',90,300,'warning'),('文件系统可用空间不足','disk',85,300,'warning'),('inode 使用率过高','inode',85,300,'warning'),('采集器持续失败','collector_failures',0,300,'warning')]:
        MonitorRule.objects.create(name=name,metric=metric,threshold=threshold,duration=duration,severity=severity)
