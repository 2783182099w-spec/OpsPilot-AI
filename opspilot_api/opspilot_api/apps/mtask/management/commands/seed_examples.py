from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from host.models import Finding, Host, HostCategory
from monitoring.models import MonitorTarget, ServiceCheck
from mtask.diagnostics import DEFAULT_TREE, tree_definition
from mtask.managed import validate_definition
from mtask.models import (
    CmdTemplate, CmdTemplateCategory, DiagnosticPolicy, DiagnosticTree, DiagnosticTreeRevision,
    ExecutionTask, HostResult, JobScript, ManagedJob, ManagedJobRevision, ReleaseApplication, ScriptRevision,
    TaskSchedule, TemplateRevision,
)


class Command(BaseCommand):
    help = '填充可辨认的示例资产和停用的配置，不执行远端任务。'

    def add_arguments(self, parser):
        parser.add_argument('--owner', required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        owner = get_user_model().objects.filter(username=options['owner'], is_superuser=True, is_active=True).first()
        if not owner:
            raise CommandError('请选择已有的管理员作为示例所有者')
        category, _ = HostCategory.objects.get_or_create(name='示例 · 测试资产')
        host, _ = Host.objects.get_or_create(name='示例 · Linux Web 节点',ip_addr='192.0.2.10',defaults={
            'category':category,'username':'ops','port':22,'environment':'test','business':'样例 Web',
            'asset_status':'maintenance','purpose':'保留地址。复制配置后替换为授权测试主机。',
        })
        cmd_category, _ = CmdTemplateCategory.objects.get_or_create(name='示例 · Linux 只读')
        command, _ = CmdTemplate.objects.get_or_create(name='示例 · 查看系统负载',category=cmd_category,
            defaults={'cmd':'uptime','description':'只读查看运行时长和系统负载'})
        TemplateRevision.objects.get_or_create(template=command,version=1,defaults={
            'command':'uptime','description':'只读查看运行时长和系统负载',
        })
        scripts = (
            ('Shell 磁盘概览','shell','#!/usr/bin/env bash\nset -eu\ndf -hP\n'),
            ('Python 系统概览','python','import platform\nimport shutil\nprint(platform.release())\nprint(shutil.disk_usage("/"))\n'),
        )
        for name, language, source in scripts:
            script, _ = JobScript.objects.get_or_create(name='示例 · '+name,owner=owner,defaults={
                'language':language,'source':source,'description':'只读验收样例',
            })
            ScriptRevision.objects.get_or_create(script=script,version=1,defaults={
                'language':language,'source':source,'description':'只读验收样例',
            })
        definition = validate_definition({
            'name':'示例 · 首批检查系统负载','kind':'command','risk':'read_only','source':'uptime',
            'description':'从一台测试资产开始采集负载。','allowed_host_ids':[host.pk],
        })
        job = ManagedJob.objects.filter(owner=owner,definition__name=definition['name']).first()
        if not job:
            job = ManagedJob.objects.create(owner=owner,definition=definition)
            ManagedJobRevision.objects.create(job=job,version=1,definition=definition,state='draft',actor=owner)
        tree, _ = DiagnosticTree.objects.get_or_create(name='示例 · Nginx HTTP 与单元',defaults={
            'definition':tree_definition(DEFAULT_TREE),
        })
        DiagnosticTreeRevision.objects.get_or_create(tree=tree,version=1,defaults={
            'actor':owner,'name':tree.name,'definition':tree.definition,
        })
        probe, _ = ServiceCheck.objects.get_or_create(host=host,port=80,path='/',defaults={
            'owner':owner,'name':'示例 · HTTP 存活','mode':'approval','unit':'nginx.service','tree':tree,'enabled':False,
        })
        DiagnosticPolicy.objects.get_or_create(name='示例 · Nginx 异常后申请审批',defaults={
            'actor':owner,'enabled':False,'config':{
                'source':'service','host_ids':[host.pk],'signal':str(probe.pk),'tree':'nginx','revision':None,
                'mode':'approval','trigger':'event','interval':15,'branch':'service_down','fallback':True,
            },
        })
        TaskSchedule.objects.get_or_create(owner=owner,name='示例 · 每小时健康巡检',defaults={
            'host_ids':[host.pk],'kind':'inspection','options':{'profile':'health','services':[],'certificates':[]},
            'interval_minutes':60,'enabled':False,'next_run':timezone.now(),
        })
        MonitorTarget.objects.get_or_create(host=host,defaults={'owner':owner,'endpoint':'192.0.2.10:9100','enabled':False})
        ReleaseApplication.objects.get_or_create(application='example-web',defaults={
            'enabled':False,'updated_by':owner,'target':{
                'host_ids':[host.pk],'environment':'test','unit':'example-web.service',
                'origin':'https://artifacts.example.com','port':8080,'path':'/health',
            },
        })
        Finding.objects.get_or_create(dedupe_key='opspilot-example-http',defaults={
            'owner':owner,'host':host,'title':'示例 · HTTP 503 排查','source':'external','condition':'unknown',
            'evidence':'模拟样例：网关返回 HTTP 503。下一步核对上游监听和服务状态。',
        })
        if not ExecutionTask.objects.filter(owner=owner,options__example='health-report').exists():
            now = timezone.now()
            task = ExecutionTask.objects.create(owner=owner,kind='inspection',command='',host_ids=[host.pk],
                options={'profile':'health','example':'health-report'},status='completed',completed_hosts=1,
                started_at=now,finished_at=now)
            HostResult.objects.create(task=task,host_id_snapshot=host.pk,host_info={'id':host.pk,'name':host.name},
                status='success',finished_at=now,checks=[{
                    'id':'cpu','title':'示例 · CPU 复检','state':'pass','summary':'模拟数据 18%，低于阈值','evidence':'样例记录',
                }])
        self.stdout.write(self.style.SUCCESS('示例已就绪。'))
