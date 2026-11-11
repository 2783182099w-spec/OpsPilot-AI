from datetime import datetime, timezone as utc_timezone
from django.utils import timezone
from django.db import transaction
from django.utils.dateparse import parse_datetime
from host.models import Finding, FindingNote
from .models import MonitorTarget, MonitorRule, MonitorHealth
from .prometheus import api, latest, Unavailable


def sync_alerts():
    now = timezone.now()
    health, _ = MonitorHealth.objects.get_or_create(name='default')
    health.heartbeat_at = now
    try:
        targets = {t.host_id:t for t in MonitorTarget.objects.select_related('host').filter(enabled=True,host__is_deleted=False)}
        samples = latest(list(targets))
        groups = api('/api/v1/rules', {'type':'alert'}).get('groups', [])
        configured = {r.id:r for r in MonitorRule.objects.filter(enabled=True)}
        loaded, seen = set(), set()
        with transaction.atomic():
            for group in groups:
                for rule in group.get('rules', []):
                    labels = rule.get('labels', {})
                    if labels.get('source') != 'opspilot':
                        continue
                    try:
                        rule_id = int(labels.get('rule_id',''))
                    except ValueError:
                        continue
                    if rule_id not in configured:
                        continue
                    evaluated = parse_datetime(rule.get('lastEvaluation',''))
                    if rule.get('health') != 'ok' or evaluated is None or now.timestamp()-evaluated.timestamp()>120:
                        raise Unavailable('Prometheus 告警规则计算异常，暂不推断恢复')
                    loaded.add(rule_id)
                    for alert in rule.get('alerts', []):
                        try:
                            host_id = int(alert.get('labels', {}).get('host_id',''))
                        except ValueError:
                            continue
                        if host_id not in targets or alert.get('state') != 'firing':
                            continue
                        target, definition = targets[host_id], configured[rule_id]
                        key = f'monitor:{host_id}:{rule_id}'
                        seen.add(key)
                        finding, created = Finding.objects.get_or_create(dedupe_key=key, defaults={'owner_id':target.owner_id,'host_id':host_id,'title':definition.name,'source':'monitor','condition':'active','severity':definition.severity})
                        if not created and finding.condition == 'recovered':
                            finding.occurrences += 1
                            finding.state = 'open'
                            FindingNote.objects.create(finding=finding, text='监控再次触发，重新打开处理记录')
                        finding.condition, finding.recovered_at = 'active', None
                        finding.severity, finding.title = definition.severity, definition.name
                        finding.evidence = f'指标：{definition.metric}；阈值：{definition.threshold}；持续：{definition.duration} 秒；恢复保持：{definition.recovery_seconds} 秒；当前告警值：{alert.get("value", "未知")}；开始：{alert.get("activeAt", "未知")}'
                        measured = samples.get(host_id,{})
                        sample_time = measured.get('sample_time')
                        finding.signal = {'metric':definition.metric,'value':measured.get(definition.metric),
                                          'threshold':definition.threshold,'duration':definition.duration,
                                          'recovery_seconds':definition.recovery_seconds,
                                          'sample_time':datetime.fromtimestamp(sample_time,utc_timezone.utc).isoformat() if sample_time else None}
                        finding.save()
            for finding in Finding.objects.filter(source='monitor').exclude(condition='recovered'):
                if finding.dedupe_key in seen:
                    continue
                try:
                    rule_id = int(finding.dedupe_key.rsplit(':',1)[1])
                except ValueError:
                    continue
                definition = configured.get(rule_id)
                sample = samples.get(finding.host_id, {})
                valid = definition and rule_id in loaded and finding.host_id in targets and sample.get('up') == 1 and sample.get(definition.metric) is not None and now.timestamp()-sample.get('sample_time',0)<=180
                if valid:
                    finding.condition, finding.recovered_at = 'recovered', now
                    FindingNote.objects.create(finding=finding, text='监控规则已恢复；保留人工处理状态，可复核后关闭')
                else:
                    finding.condition = 'unknown'
                finding.save(update_fields=['condition','recovered_at'])
        health.last_success_at, health.error = now, ''
    except Unavailable as exc:
        health.error = str(exc)[:500]
        Finding.objects.filter(source='monitor').exclude(condition='recovered').update(condition='unknown')
    health.save(update_fields=['heartbeat_at','last_success_at','error'])
    return not bool(health.error)
