"""Browser validation with loopback assets, a gated fixture and no task approval."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'opspilot_api'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','opspilot_api.settings.local')
if os.environ['DJANGO_SETTINGS_MODULE']!='opspilot_api.settings.local':
    raise SystemExit('Local settings required')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from host.models import Host
from mtask.managed import prepare
from mtask.models import ExecutionTask,ManagedJob
from mtask.task_views import enqueue

if settings.DATABASES['default']['ENGINE']!='django.db.backends.sqlite3':
    raise SystemExit('Only a local SQLite database is supported')
node=os.environ.get('OPSPILOT_NODE') or shutil.which('node')
if not node:
    raise SystemExit('Node.js not found')
key='rollout_smoke_'+secrets.token_hex(5)
password=secrets.token_urlsafe(24)
users=get_user_model()
admin=users.objects.create_superuser(key+'_admin',password=password)
operator=users.objects.create_user(key,password=password)
hosts=[]
try:
    for n in range(2):
        host=Host.objects.create(name=key+'-'+str(n+1),ip_addr='127.0.0.1',port=22222,username='fixture',environment='test')
        host.users.add(operator)
        hosts.append(host)
    job=ManagedJob.objects.create(owner=admin,state='published',definition={
        'name':key+'-gate','description':'离线测试任务，不连接远端','kind':'shell','risk':'read_only',
        'parameters':[],'environments':['test'],'allowed_host_ids':[h.id for h in hosts],
        'verify_service':'','ssh_user':'','os_match':'','source':'true','timeout':30,
        'concurrency':1,'canary_count':1,'batch_size':2,'failure_limit':1})
    source,options=prepare(job,hosts,{},'离线界面验证')
    held=enqueue(operator,[h.id for h in hosts],'script',source,options,True)
    held.host_results.filter(host_id_snapshot=hosts[0].id).update(status='success',may_have_run=True,batch=1,output='offline fixture',stages=[
        {'name':'connect','status':'success','elapsed_ms':24,'detail':'隔离样本'},
        {'name':'execute','status':'success','elapsed_ms':52,'detail':'退出码 0'}])
    ExecutionTask.objects.filter(pk=held.id).update(status='awaiting_release',started_at=timezone.now(),completed_hosts=1,stop_reason='首批通过，等待人工核对并放行')
    with tempfile.TemporaryDirectory(prefix='opspilot-managed-smoke-') as directory:
        fixture=Path(directory)/'fixture.json'
        fixture.write_text(json.dumps({'admin':admin.username,'operator':operator.username,'password':password,
            'host_names':[h.name for h in hosts],'held':held.id,'job':key+'-new'}),encoding='utf8')
        env=dict(os.environ,OPSPILOT_MANAGED_FIXTURE=str(fixture))
        code=subprocess.call([node,str(root/'scripts/smoke-managed.cjs')],env=env)
finally:
    ExecutionTask.objects.filter(owner_id__in=[operator.id,admin.id]).delete()
    ManagedJob.objects.filter(owner=admin).delete()
    for host in hosts:
        host.delete()
    operator.delete()
    admin.delete()
sys.exit(code)
