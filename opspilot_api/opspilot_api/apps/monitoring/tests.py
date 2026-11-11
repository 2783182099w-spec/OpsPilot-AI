from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from host.models import Host, Finding
from mtask.models import ExecutionTask, HostResult
from .models import MonitorTarget, MonitorRule, MonitorHealth
from .configuration import validate_endpoint, write_config
from .prometheus import expressions, Unavailable, finite
from .sync import sync_alerts


class MonitoringTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('monitor-admin',password='test-only')
        self.user=get_user_model().objects.create_user('monitor-user')
        self.other=get_user_model().objects.create_user('monitor-other')
        self.host=Host.objects.create(name='host-test',ip_addr='127.0.0.1',port=22222,username='test')
        self.host.users.add(self.user)
        self.target=MonitorTarget.objects.create(host=self.host,owner=self.admin,endpoint='127.0.0.1:19100')
        self.rule=MonitorRule.objects.create(name='CPU test',metric='cpu',threshold=90,duration=300)
        self.client=APIClient()
        self.client.force_authenticate(self.user)

    def rule_data(self, active=True, health='ok'):
        return {'groups':[{'rules':[{'labels':{'source':'opspilot','rule_id':str(self.rule.id)},'health':health,'lastEvaluation':timezone.now().astimezone().isoformat(),'alerts':[{'state':'firing','labels':{'host_id':str(self.host.id)},'value':'95','activeAt':'test'}] if active else []}]}]}

    def test_read_permissions_and_configuration_admin_only(self):
        with patch('monitoring.views.latest',return_value={self.host.id:{'up':1,'cpu':42,'sample_time':timezone.now().timestamp()}}):
            response=self.client.get('/monitoring/overview')
            self.assertEqual(response.data['rows'][0]['metrics']['cpu'],42)
            self.assertEqual(response.data['rows'][0]['state'],'up')
            self.assertEqual(self.client.patch(f'/monitoring/targets/{self.host.id}',{'endpoint':'127.0.0.1:19100'},format='json').status_code,403)
            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.get('/monitoring/overview').data['rows'],[])
            self.assertEqual(self.client.get(f'/monitoring/hosts/{self.host.id}/history').status_code,404)
            self.assertEqual(self.client.get(f'/monitoring/hosts/{self.host.id}/workbench').status_code,404)

    def test_failure_is_unknown_not_zero_or_healthy(self):
        with patch('monitoring.views.latest',side_effect=Unavailable('offline')):
            data=self.client.get('/monitoring/overview').data
            self.assertEqual(data['rows'][0]['state'],'unknown')
            self.assertEqual(data['rows'][0]['metrics'],{})
        with patch('monitoring.views.latest',return_value={self.host.id:{'up':1,'sample_time':timezone.now().timestamp()-400}}):
            self.assertEqual(self.client.get('/monitoring/overview').data['rows'][0]['state'],'stale')

    def test_history_bounds_metric_and_authorization(self):
        self.assertEqual(self.client.get(f'/monitoring/hosts/{self.host.id}/history?metric=arbitrary_promql').status_code,400)
        self.assertEqual(self.client.get(f'/monitoring/hosts/{self.host.id}/history?hours=169').status_code,400)
        with patch('monitoring.views.history',return_value=[[1,22]]) as history:
            self.assertEqual(self.client.get(f'/monitoring/hosts/{self.host.id}/history?metric=cpu&hours=6').status_code,200)
            history.assert_called_once_with(self.host.id,'cpu',6)
        self.assertIn('host_id=~"(1|2)"',expressions([1,2])['cpu'])
        self.assertIsNone(finite('NaN'))
        self.assertIsNone(finite('+Inf'))

    def test_endpoint_validation_and_rule_limits(self):
        for value in ['http://127.0.0.1:9100/x','169.254.169.254:80','0.0.0.0:9100','127.0.0.1:99999','user:pass@host:9100']:
            with self.assertRaises(ValidationError):
                validate_endpoint(value)
        self.client.force_authenticate(self.admin)
        response=self.client.patch(f'/monitoring/rules/{self.rule.id}',{'threshold':101},format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.patch(f'/monitoring/rules/{self.rule.id}',{'duration':-1},format='json').status_code,400)

    def test_configuration_is_scoped_and_retains_duration(self):
        import json
        with TemporaryDirectory() as directory, override_settings(MONITOR_CONFIG_DIR=Path(directory)):
            self.assertEqual(write_config(),(1,1))
            rules=json.loads((Path(directory)/'rules.yml').read_text())['groups'][0]['rules']
            self.assertEqual(rules[0]['for'],'300s')
            self.assertEqual(rules[0]['keep_firing_for'],'120s')
            self.assertIn('and on(host_id)',rules[0]['expr'])
            self.host.is_deleted=True
            self.host.save()
            self.assertEqual(write_config()[0],0)

    @patch('monitoring.sync.latest')
    @patch('monitoring.sync.api')
    def test_alert_dedup_recovery_and_recurrence(self,api,latest):
        latest.return_value={self.host.id:{'up':1,'cpu':95,'sample_time':timezone.now().timestamp()}}
        api.return_value=self.rule_data()
        self.assertTrue(sync_alerts())
        sync_alerts()
        self.assertEqual(Finding.objects.count(),1)
        alert=Finding.objects.get()
        self.assertEqual(alert.condition,'active')
        self.assertEqual(alert.occurrences,1)
        alert.state='processing'
        alert.save()
        api.return_value=self.rule_data(active=False)
        latest.return_value={self.host.id:{'up':1,'cpu':10,'sample_time':timezone.now().timestamp()}}
        sync_alerts()
        alert.refresh_from_db()
        self.assertEqual(alert.condition,'recovered')
        self.assertEqual(alert.state,'processing')
        api.return_value=self.rule_data()
        sync_alerts()
        alert.refresh_from_db()
        self.assertEqual(alert.condition,'active')
        self.assertEqual(alert.occurrences,2)
        self.assertEqual(alert.state,'open')
        self.assertEqual(self.client.get('/host/findings?page=1&source=monitor').data['count'],1)

    @patch('monitoring.sync.latest')
    @patch('monitoring.sync.api')
    def test_missing_metrics_outage_or_bad_rule_never_proves_recovery(self,api,latest):
        api.return_value=self.rule_data()
        latest.return_value={self.host.id:{'up':1,'cpu':95,'sample_time':timezone.now().timestamp()}}
        sync_alerts()
        alert=Finding.objects.get()
        for samples in ({self.host.id:{'up':0}}, {self.host.id:{'up':1}}, {self.host.id:{'up':1,'cpu':10,'sample_time':timezone.now().timestamp()-400}}):
            latest.return_value=samples
            api.return_value=self.rule_data(active=False)
            sync_alerts()
            alert.refresh_from_db()
            self.assertEqual(alert.condition,'unknown')
            self.assertIsNone(alert.recovered_at)
        api.side_effect=Unavailable('offline')
        self.assertFalse(sync_alerts())
        self.assertTrue(MonitorHealth.objects.get().error)

    def test_worker_lock_prevents_duplicate_syncers_and_releases(self):
        from monitoring.locking import worker_lock
        from django.core.management.base import CommandError
        with TemporaryDirectory() as directory, override_settings(MONITOR_CONFIG_DIR=Path(directory)):
            with worker_lock():
                with self.assertRaises(CommandError):
                    with worker_lock():
                        pass
            with worker_lock():
                pass

    def test_human_close_and_maintenance_do_not_change_fault_condition(self):
        alert=Finding.objects.create(owner=self.admin,host=self.host,source='monitor',dedupe_key='monitor-test',title='test',condition='active')
        response=self.client.patch(f'/host/findings/{alert.id}',{'state':'resolved','silence_minutes':60,'note':'test'},format='json')
        self.assertEqual(response.status_code,200)
        alert.refresh_from_db()
        self.assertEqual(alert.condition,'active')
        self.assertGreater(alert.silenced_until,timezone.now())


class HealthReportTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('report-user')
        self.other=get_user_model().objects.create_user('report-other')
        self.host=Host.objects.create(name='<script>bad</script>',ip_addr='127.0.0.1',port=22,username='test')
        self.task=ExecutionTask.objects.create(owner=self.user,kind='inspection',host_ids=[self.host.id],command='',status='completed',options={'profile':'health','purpose':'保障巡检'})
        self.result=HostResult.objects.create(task=self.task,host_id_snapshot=self.host.id,host_info={'name':self.host.name,'ip_addr':'127.0.0.1'},status='success',checks=[{'id':'disk','title':'Disk','state':'pass','summary':'normal','evidence':'<script>alert(1)</script>'}])
        self.client=APIClient()
        self.client.force_authenticate(self.user)

    def test_report_safe_html_health_scope_and_no_false_normal(self):
        response=self.client.get(f'/mtask/tasks/{self.task.id}/health-report')
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertIn('本次所列检查范围内未发现异常',html)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn("default-src 'none'",response['Content-Security-Policy'])
        self.result.checks[0]['state']='unknown'
        self.result.save()
        self.assertNotIn('本次所列检查范围内未发现异常',self.client.get(f'/mtask/tasks/{self.task.id}/health-report').content.decode())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/mtask/tasks/{self.task.id}/health-report').status_code,404)

    def test_comparison_same_scope_and_removed_check_not_assumed_recovered(self):
        task=ExecutionTask.objects.create(owner=self.user,kind='inspection',host_ids=[self.host.id],command='',status='completed',options={'profile':'health'})
        HostResult.objects.create(task=task,host_id_snapshot=self.host.id,host_info={'name':'test'},status='failed',checks=[])
        response=self.client.get(f'/mtask/tasks/{task.id}/comparison')
        self.assertEqual(response.data['previous_id'],self.task.id)
        self.assertEqual(response.data['changes'][0]['after'],'本次未返回，不能判断恢复')


class DashboardTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('screen-user')
        self.other=get_user_model().objects.create_user('screen-other')
        self.host=Host.objects.create(name='visible',ip_addr='127.0.0.1',port=22,username='test')
        self.hidden=Host.objects.create(name='hidden',ip_addr='127.0.0.2',port=22,username='test')
        self.host.users.add(self.user)
        MonitorTarget.objects.create(owner=self.user,host=self.host,endpoint='127.0.0.1:19100')
        self.client=APIClient();self.client.force_authenticate(self.user)

    @patch('monitoring.dashboard.fleet_history',return_value={'cpu':[],'memory':[]})
    @patch('monitoring.views.latest')
    def test_scope_success_rate_and_script_sources_not_exposed(self,latest,history):
        latest.return_value={self.host.id:{'up':1,'sample_time':timezone.now().timestamp(),'cpu':20,'memory':30}}
        for state in ('completed','failed','cancelled','awaiting_approval'):
            ExecutionTask.objects.create(owner=self.user,kind='script',command='private code',host_ids=[self.host.id],status=state)
        ExecutionTask.objects.create(owner=self.other,kind='script',command='hidden code',host_ids=[self.hidden.id],status='completed')
        Finding.objects.create(owner=self.other,host=self.hidden,dedupe_key='hidden',title='hidden alert',condition='active')
        data=self.client.get('/monitoring/dashboard').data
        self.assertEqual(data['scope']['asset_total'],1)
        self.assertEqual(data['collection']['up'],1)
        self.assertEqual(data['tasks']['success_rate'],50)
        self.assertEqual(data['tasks']['ended_24h'],2)
        self.assertEqual(data['alerts']['active'],0)
        self.assertEqual(data['top']['cpu'][0]['id'],self.host.id)
        self.assertNotIn('private code',str(data))
        self.assertNotIn('hidden code',str(data))
        history.assert_called_once_with([self.host.id])

    @patch('monitoring.dashboard.fleet_history')
    @patch('monitoring.views.latest',side_effect=Unavailable('offline'))
    def test_outage_does_not_erase_inventory_or_invent_healthy_metrics(self,latest,history):
        data=self.client.get('/monitoring/dashboard').data
        self.assertEqual(data['scope']['asset_total'],1)
        self.assertEqual(data['collection']['unknown'],1)
        self.assertEqual(data['top']['cpu'],[])
        self.assertIsNone(data['tasks']['success_rate'])
        self.assertEqual(data['error'],'offline')
        history.assert_not_called()


class ConfigFileTests(TestCase):
    def test_transient_windows_lock_retries_without_corrupting_config(self):
        from monitoring.configuration import atomic_json
        import json
        import os
        replace=os.replace
        count=0
        def flaky(source,target):
            nonlocal count
            count+=1
            if count<3:
                raise PermissionError('simulated file busy')
            return replace(source,target)
        with TemporaryDirectory() as directory, patch('monitoring.configuration.os.replace',side_effect=flaky),patch('monitoring.configuration.time.sleep'):
            target=Path(directory)/'targets.json'
            atomic_json(target,[{'targets':['127.0.0.1:9100']}])
            self.assertEqual(json.loads(target.read_text())[0]['targets'],['127.0.0.1:9100'])
            self.assertEqual(count,3)
            self.assertEqual(len(list(Path(directory).iterdir())),1)

    def test_persistent_lock_keeps_previous_config_and_removes_temp(self):
        from monitoring.configuration import atomic_json
        with TemporaryDirectory() as directory,patch('monitoring.configuration.os.replace',side_effect=PermissionError('busy')),patch('monitoring.configuration.time.sleep'):
            target=Path(directory)/'targets.json'
            target.write_text('[]')
            with self.assertRaises(PermissionError):atomic_json(target,[1])
            self.assertEqual(target.read_text(),'[]')
            self.assertEqual(len(list(Path(directory).iterdir())),1)
