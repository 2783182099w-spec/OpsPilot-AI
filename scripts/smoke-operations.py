"""可选的本地浏览器冒烟测试：创建并清理隔离测试数据，不连接远端。"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'opspilot_api'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'opspilot_api.settings.local')
if os.environ['DJANGO_SETTINGS_MODULE'] != 'opspilot_api.settings.local':
    raise RuntimeError('只允许在本地配置下运行')
import django
django.setup()
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
from host.models import Host, HostCategory, Finding, FileTransfer, SSHSession
from mtask.models import CmdTemplate, CmdTemplateCategory, ExecutionTask, TaskSchedule, HostResult, JobScript

if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
    raise RuntimeError('本脚本仅支持本地 SQLite 测试，拒绝对外部数据库创建测试数据')
node = os.environ.get('OPSPILOT_NODE') or shutil.which('node')
if not node:
    raise RuntimeError('需要 Node.js 和可选 Playwright 测试环境')
name = 'ops_smoke_' + secrets.token_hex(8)
password = secrets.token_urlsafe(32)
with transaction.atomic():
    user = get_user_model().objects.create_user(name, password=password)
    admin = get_user_model().objects.create_superuser(name+'_admin',password=password)
    category = HostCategory.objects.create(name=name)
    host = Host.objects.create(name=name, category=category, ip_addr='127.0.0.1', port=22222, username='test', environment='staging', purpose='offline browser test only')
    host.users.add(user)
    template_category = CmdTemplateCategory.objects.create(name=name)
    template = CmdTemplate.objects.create(name=name, category=template_category, cmd='systemctl status {{service}}', parameters=[{'name':'service','type':'enum','choices':['sshd','nginx']}])
    session = SSHSession.objects.create(owner=user,host=host,host_label=name,transcript='offline text fixture\nwhoami\ntest\n',recorded_bytes=33,status='closed')
    task = ExecutionTask.objects.create(owner=user,kind='inspection',command='',host_ids=[host.id],status='completed',completed_hosts=1)
    HostResult.objects.create(task=task,host_id_snapshot=host.id,host_info={'id':host.id,'name':name,'ip_addr':'127.0.0.1'},status='success',checks=[{'id':'disk','title':'disk','state':'warning','summary':'fixture high usage','evidence':'90%'}])
    issue = Finding.objects.create(host=host, owner=user, dedupe_key=name, title=name, evidence='offline browser fixture')
fd, filename = tempfile.mkstemp(prefix='opspilot-ops-', suffix='.json')
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump({'username':name,'admin_username':admin.username,'password':password,'host_id':host.id,'template_id':template.id,'finding_id':issue.id,'session_id':session.id,'inspection_id':task.id}, output)
    env = dict(os.environ, OPSPILOT_SMOKE_FIXTURE=filename)
    result = subprocess.run([node, str(root/'scripts/smoke-operations.cjs')], env=env)
finally:
    SSHSession.objects.filter(owner=user).delete()
    TaskSchedule.objects.filter(owner=user).delete()
    ExecutionTask.objects.filter(owner=user).delete()
    Finding.objects.filter(owner=user).delete()
    FileTransfer.objects.filter(owner=user).delete()
    Host.objects.filter(pk=host.pk, users=user).delete()
    HostCategory.objects.filter(pk=category.pk, name=name).delete()
    CmdTemplate.objects.filter(category=template_category).delete()
    CmdTemplateCategory.objects.filter(pk=template_category.pk, name=name).delete()
    JobScript.objects.filter(owner__in=[user,admin]).delete()
    user.delete()
    admin.delete()
    Path(filename).unlink(missing_ok=True)
sys.exit(result.returncode)
