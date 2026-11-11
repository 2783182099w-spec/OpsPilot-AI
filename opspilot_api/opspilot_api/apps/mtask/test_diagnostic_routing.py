import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from host.models import Finding, Host
from monitoring.models import MonitorTarget
from .diagnostic_routes import check_monitoring
from .diagnostic_script import filesystem_evidence
from .diagnostics import recommended_tree
from .models import ExecutionTask
from .tasks import record_findings, run_task


class DiagnosticRoutingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('diagnostic-routing')
        self.host = Host.objects.create(name='diagnosis fixture',ip_addr='192.0.2.15',port=22,username='ops')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def finding(self, source='monitor', **fields):
        return Finding.objects.create(owner=self.user,host=self.host,title='fixture',condition='active',
                                      dedupe_key=fields.pop('dedupe_key',source),source=source,**fields)

    def post(self, finding):
        return self.client.post(f'/mtask/incidents/{finding.pk}/runs',{'confirmed':True},format='json')

    def test_monitor_metrics_have_specific_routes(self):
        finding = self.finding(signal={'metric':'cpu','threshold':91})
        plan = recommended_tree(finding)
        self.assertEqual(plan.definition['nodes'][0]['threshold'],91)
        self.assertEqual(plan.definition['nodes'][1]['kind'],'resource_context')
        for metric in ('disk','inode'):
            finding.signal = {'metric':metric,'threshold':85}
            node = recommended_tree(finding).definition['nodes'][0]
            self.assertTrue(node['all_mounts'])
        finding.signal = {}
        finding.evidence = '指标：memory；阈值：89；持续：300 秒'
        self.assertEqual(recommended_tree(finding).definition['nodes'][0]['metric'],'memory')

    def test_old_inspection_checks_reuse_original_service_target(self):
        task = ExecutionTask.objects.create(owner=self.user,command='',kind='inspection',host_ids=[self.host.pk],
            options={'profile':'health','services':['example.service'],'certificates':[]})
        finding = self.finding(source='inspection',source_task=task,
                               dedupe_key=f'{self.user.pk}:{self.host.pk}:service_0')
        response = self.post(finding)
        self.assertEqual(response.status_code,202,response.data)
        options = ExecutionTask.objects.get(pk=response.data['task_id']).options
        self.assertEqual(options['services'],['example.service'])
        self.assertEqual(options['incident']['check_id'],'service_0')
        record_findings(task,self.host.pk,[{'id':'time_sync','title':'时间同步','state':'warning','summary':'未同步'}])
        self.assertEqual(Finding.objects.get(dedupe_key__endswith=':time_sync').signal,{'check_id':'time_sync'})

    @patch('mtask.diagnostic_routes.check_monitoring', return_value=('failed','Exporter fixture is unreachable'))
    @patch('mtask.tasks.SSHParamiko')
    def test_prometheus_diagnosis_does_not_open_ssh_or_rewrite_finding(self, ssh, check):
        finding = self.finding(signal={'metric':'up','threshold':1})
        response = self.post(finding)
        self.assertEqual(response.status_code,202,response.data)
        task = ExecutionTask.objects.get(pk=response.data['task_id'])
        self.assertEqual(task.kind,'probe')
        run_task(task)
        ssh.assert_not_called()
        check.assert_called_once()
        finding.refresh_from_db()
        self.assertEqual(finding.condition,'active')
        result = self.client.get(f'/mtask/incidents/{finding.pk}/runs',{'workspace':1}).data['runs'][0]
        self.assertEqual(result['summary']['outcome'],'abnormal')
        self.assertEqual(result['checks'][0]['title'],'Prometheus 采集链路')

    @patch('monitoring.prometheus.api')
    def test_collector_diagnosis_uses_fresh_scoped_samples(self, api):
        MonitorTarget.objects.create(host=self.host,owner=self.user,endpoint='127.0.0.1:9100')
        api.side_effect = [{'result':[{'value':[0,str(time.time())]}]},
                           {'result':[{'metric':{'collector':'filesystem'},'value':[0,'0']}]}]
        state, evidence = check_monitoring(self.host,'collector_failures')
        self.assertEqual(state,'failed')
        self.assertIn('filesystem',evidence)
        query = api.call_args.args[1]['query']
        self.assertIn(f'host_id="{self.host.pk}"',query)
        self.assertIn('== 1',query)

    def test_unknown_external_alert_requires_explicit_template(self):
        finding = self.finding(source='external',evidence='指标：cpu；阈值：90；')
        response = self.client.get(f'/mtask/incidents/{finding.pk}/runs',{'workspace':1})
        self.assertIsNone(response.data['recommendation'])
        self.assertEqual(self.post(finding).status_code,400)
        self.assertFalse(ExecutionTask.objects.exists())

    @patch('mtask.diagnostic_script.subprocess.run')
    def test_mount_scan_reports_the_highest_mount_and_keeps_partial_unknown(self, run):
        run.return_value = SimpleNamespace(stdout='Use% Mounted on\n50% /\n97% /data\n',returncode=0)
        state,evidence = filesystem_evidence('disk',85)
        self.assertEqual(state,'failed')
        self.assertIn('最高挂载点 /data',evidence)
        run.return_value = SimpleNamespace(stdout='IUse% Mounted on\n50% /\n',returncode=1)
        self.assertEqual(filesystem_evidence('inode',85)[0],'unknown')
        self.assertIn('--output=ipcent,target',run.call_args.args[0])
