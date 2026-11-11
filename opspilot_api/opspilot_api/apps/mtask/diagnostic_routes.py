"""Route an alert from trusted source metadata to a scoped, read-only check."""
import math
import re
import time
from types import SimpleNamespace

from .inspection import rules

RESOURCE_NAMES = {'cpu':'CPU 使用率复检','memory':'内存压力排查','load':'负载排查',
                  'disk':'文件系统容量排查','inode':'文件系统 inode 排查'}


def recommendation(name, nodes, conclusions=None, executor='ssh', **metadata):
    return SimpleNamespace(pk=None,name=name,version=2,executor=executor,metadata=metadata,
                           definition={'root':nodes[0]['id'],'nodes':nodes,'conclusions':conclusions or {}})


def source_plan(finding):
    signal = finding.signal or {}
    if finding.source == 'inspection':
        source = finding.source_task
        check = signal.get('check_id')
        if not check:
            prefix = f'{finding.owner_id}:{finding.host_id}:'
            if finding.dedupe_key.startswith(prefix):
                check = finding.dedupe_key[len(prefix):]
        if check == 'connectivity':
            return recommendation('SSH 连通性复检',[{'id':'connectivity','kind':'connectivity'}],executor='tcp')
        options = source.options if source and source.kind=='inspection' else {}
        options = {'profile':'acceptance','services':[],'certificates':[],**options}
        row = next((item for item in rules(options) if item[0]==check),None)
        if row:
            return recommendation(row[1]+' · 专项复检',[{'id':check,'kind':'inspection','title':row[1]}],
                                  executor='inspection',check_id=check,inspection_options=options)
        return None

    if finding.source != 'monitor':
        return None
    metric = signal.get('metric')
    threshold = signal.get('threshold')
    if not metric:
        # Older monitor records already persisted the rule's metric in this field.
        match = re.match(r'^指标[：:]([a-z_]+)[；;]阈值[：:]([\d.]+)[；;]',finding.evidence)
        if match:
            metric, value = match.groups()
            try:
                threshold = float(value)
            except ValueError:
                pass
        elif re.fullmatch(r'monitor:\d+:\d+',finding.dedupe_key):
            from monitoring.models import MonitorRule
            rule = MonitorRule.objects.filter(pk=finding.dedupe_key.rsplit(':',1)[1]).first()
            if rule:
                metric, threshold = rule.metric, rule.threshold

    if metric in ('up','collector_failures'):
        name = 'Prometheus 采集链路排查' if metric=='up' else 'Node Exporter 采集器排查'
        return recommendation(name,[{'id':'check','kind':'collector' if metric=='collector_failures' else 'scrape'}],
                              executor='monitor',metric=metric)
    if metric not in RESOURCE_NAMES or type(threshold) not in (int,float) or not math.isfinite(threshold) or threshold < 0:
        return None
    if metric!='load' and threshold>100:
        return None

    nodes = [{'id':'check','kind':'resource','metric':metric,'threshold':threshold,
              'ok':'healthy','failed':'failed','unknown':'unknown'}]
    if metric in ('disk','inode'):
        nodes[0]['all_mounts'] = True
    if metric in ('cpu','memory','load'):
        nodes[0]['failed'] = 'context'
        nodes.append({'id':'context','kind':'resource_context','metric':metric,
                      'ok':'failed','failed':'failed','unknown':'partial'})
    conclusions = {
        'healthy':'当前复检未达到告警阈值，请对照告警发生时的监控趋势。',
        'failed':'当前复检仍达到告警阈值，请结合下方证据定位影响范围。',
        'unknown':'未取得可靠复检数据，当前无法确认故障。',
        'partial':'资源异常已复现，补充证据采集失败，请人工继续排查。',
    }
    return recommendation(RESOURCE_NAMES[metric],nodes,conclusions)


def check_monitoring(host, metric):
    from monitoring.models import MonitorTarget
    from monitoring.prometheus import Unavailable, api, finite
    target = MonitorTarget.objects.filter(host=host,enabled=True).first()
    if not target:
        return 'unknown','该资产没有启用 Node Exporter 采集目标'
    matcher = '{job="opspilot_nodes",host_id="%d"}' % host.pk
    try:
        rows = api('/api/v1/query',{'query':'timestamp(up'+matcher+')','timeout':'5s'}).get('result',[])
        sampled = finite(rows[0]['value'][1]) if rows else None
        if sampled is None or time.time()-sampled>180:
            return 'unknown','Prometheus 没有近期采样，请检查采集目标和同步器'
        query = ('up'+matcher if metric=='up' else
                 'node_scrape_collector_success'+matcher+' and on(host_id) (up'+matcher+' == 1)')
        rows = api('/api/v1/query',{'query':query,'timeout':'5s'}).get('result',[])
        if not rows:
            return 'unknown','没有查询到有效监控指标'
        values = [(row.get('metric',{}),finite(row['value'][1])) for row in rows]
        if any(value is None for _,value in values):
            return 'unknown','监控指标值无效'
        if metric == 'up':
            return ('ok','Prometheus 最近一次采集成功') if all(value==1 for _,value in values) else (
                'failed','Prometheus 最近一次采集失败，请核对 Exporter 服务、采集端口和链路')
        failed = [str(labels.get('collector','未知')) for labels,value in values if value!=1]
        if failed:
            return 'failed',f'{len(failed)} 个采集器失败 · '+', '.join(failed[:10])
        return 'ok','当前采集器指标均正常'
    except (Unavailable,KeyError,TypeError,IndexError):
        return 'unknown','Prometheus 查询失败，请检查服务、隧道和采集配置'
