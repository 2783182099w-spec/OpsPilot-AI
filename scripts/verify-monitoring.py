"""Live, non-destructive monitoring integration; one explicitly selected target only."""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'opspilot_api'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','opspilot_api.settings.local')
import django
django.setup()
from django.core.cache import cache
from monitoring.models import MonitorTarget, MonitorRule
from monitoring.configuration import write_config
from monitoring.prometheus import latest,api,history
from monitoring.sync import sync_alerts
from host.models import Finding
parser=argparse.ArgumentParser()
parser.add_argument('--host-id',type=int,required=True)
args=parser.parse_args()
if list(MonitorTarget.objects.filter(enabled=True).values_list('host_id',flat=True))!=[args.host_id]:
    raise RuntimeError('Live test requires exactly one selected enabled target')
cache.clear()
values=latest([args.host_id])[args.host_id]
if values.get('up')!=1 or values.get('memory') is None:
    raise RuntimeError('Live metrics not ready')
print('Real collection verified:',json.dumps(values),flush=True)
print('CPU history samples:',len(history(args.host_id,'cpu',1)),flush=True)
rule=MonitorRule.objects.create(name='__integration__'+uuid.uuid4().hex,metric='memory',threshold=0,duration=0,recovery_seconds=0)
key=f'monitor:{args.host_id}:{rule.id}'
def wait_for(state):
    deadline=time.monotonic()+50
    while time.monotonic()<deadline:
        groups=api('/api/v1/rules',{'type':'alert'})['groups']
        item=next((r for g in groups for r in g['rules'] if r.get('labels',{}).get('rule_id')==str(rule.id)),None)
        if item and item.get('state')==state:
            return
        time.sleep(3)
    raise RuntimeError('Expected rule state not observed: '+state)
try:
    write_config();api('/-/reload',post=True)
    wait_for('firing')
    sync_alerts();sync_alerts()
    issue=Finding.objects.get(dedupe_key=key)
    if issue.condition!='active' or Finding.objects.filter(dedupe_key=key).count()!=1:
        raise RuntimeError('Alert firing/dedup failed')
    print('Real Prometheus firing -> platform alert -> dedup verified',flush=True)
    rule.threshold=100
    rule.save(update_fields=['threshold'])
    write_config();api('/-/reload',post=True)
    wait_for('inactive')
    cache.clear();sync_alerts()
    issue.refresh_from_db()
    if issue.condition!='recovered':
        raise RuntimeError('Recovery sync failed')
    print('Real rule recovery -> platform recovery verified; no server fault injected',flush=True)
finally:
    Finding.objects.filter(dedupe_key=key,source='monitor').delete()
    rule.delete()
    write_config();api('/-/reload',post=True)
    print('Temporary rule and alert records cleaned',flush=True)
