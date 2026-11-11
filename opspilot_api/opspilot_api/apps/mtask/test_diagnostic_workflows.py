
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from host.models import Finding, Host
from monitoring.models import ServiceCheck
from .diagnostic_workflows import dispatch
from .diagnostics import DEFAULT_TREE
from .models import DiagnosticWorkflowRun, WorkerLease

FAILED_NGINX = (
    '[http] http · failed · HTTP 503\n'
    '[unit] systemd · failed · inactive\n'
    '诊断分支：service_down\n诊断结论：服务未运行\n'
)
User = get_user_model()


class DiagnosticWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('policy-owner')
        self.reviewer = User.objects.create_superuser('policy-reviewer')
        self.operator = User.objects.create_user('policy-operator')
        self.host = Host.objects.create(name='fixture',ip_addr='192.0.2.20',port=22,username='ops',environment='test')
        self.host.users.add(self.operator)
        self.probe = ServiceCheck.objects.create(host=self.host,owner=self.admin,name='nginx fixture',unit='nginx.service',
                                                 port=80,path='/',enabled=True,mode='approval',state='down',last_checked_at=timezone.now())
        self.finding = Finding.objects.create(host=self.host,owner=self.admin,title='fixture',condition='active',
                                              source='service',dedupe_key=f'service:{self.probe.pk}',
                                              signal={'check_id':self.probe.pk,'metric':'HTTP'})
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.config = {'name':'Nginx fixture','enabled':True,'source':'service','host_ids':[self.host.pk],
                       'signal':str(self.probe.pk),'trigger':'event','tree':'nginx',
                       'mode':'approval','branch':'service_down','fallback':True}

    def save(self, **changes):
        return self.client.post('/mtask/diagnostic-policies',{**self.config,**changes},format='json')

    def start(self, **changes):
        response = self.save(**changes)
        self.assertEqual(response.status_code,201,response.data)
        dispatch()
        return DiagnosticWorkflowRun.objects.get()

    def complete(self, run, output=FAILED_NGINX):
        task = run.diagnosis.task
        task.status = 'completed'
        task.finished_at = timezone.now()
        task.save(update_fields=['status','finished_at'])
        task.host_results.update(status='success',output=output)
        dispatch()
        run.refresh_from_db()

    def action(self, run, action='approve'):
        return self.client.post(f'/mtask/tasks/{run.repair.task_id}/action',{'action':action,'confirmed':True})

    def test_catalog_and_policy_permissions(self):
        self.client.force_authenticate(self.operator)
        data = self.client.get('/mtask/diagnostic-trees').data
        names = {row['key'] for row in data['builtins']}
        self.assertTrue({'监控:cpu','监控:inode','巡检:ssh','服务探测:nginx'} <= names)
        self.assertEqual(len(names),len(data['builtins']))
        self.assertEqual(self.save().status_code,403)
        self.assertEqual(self.client.get('/mtask/diagnostic-policies').status_code,403)

    def test_approval_is_independent_and_external_recovery_is_required(self):
        run = self.start()
        dispatch()
        self.assertEqual(self.finding.diagnostic_runs.count(),1)
        self.complete(run)
        self.assertEqual((run.status,run.branch),('repairing','service_down'))
        self.assertEqual(run.repair.task.status,'awaiting_approval')
        self.assertEqual(self.action(run).status_code,403)
        self.client.force_authenticate(self.reviewer)
        workspace = self.client.get(f'/mtask/incidents/{self.finding.pk}/runs?workspace=1').data
        self.assertTrue(workspace['workflow']['can_approve'])
        self.assertEqual(self.action(run).data['status'],'pending')
        task = run.repair.task
        task.status, task.finished_at = 'completed', timezone.now()
        task.save(update_fields=['status','finished_at'])
        dispatch()
        run.refresh_from_db()
        self.assertEqual(run.status,'verifying')
        self.finding.condition = 'recovered'
        self.finding.save(update_fields=['condition'])
        dispatch()
        run.refresh_from_db()
        self.assertEqual(run.status,'recovered')

    def test_approval_cannot_bypass_policy_change_or_legacy_entry(self):
        run = self.start()
        self.complete(run)
        response = self.client.post(f'/mtask/incidents/{self.finding.pk}/runs',{'confirmed':True,'mode':'repair'})
        self.assertEqual(response.status_code,400)
        response = self.client.post(f'/mtask/diagnostic-policies/{run.policy_id}',
            {'name':run.policy.name,'version':1,'enabled':False,'disable_only':True},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.action(run).status_code,400)
        dispatch()
        run.refresh_from_db()
        self.assertEqual(run.status,'manual')
        self.assertEqual(run.repair.task.status,'cancelled')

    @override_settings(OPSPILOT_AUTOMATIC_REPAIR=False)
    def test_auto_is_blocked_when_global_switch_is_off(self):
        run = self.start(mode='auto')
        self.complete(run)
        self.assertEqual(run.status,'manual')
        self.assertFalse(run.repair_id)
        self.finding.refresh_from_db()
        self.assertTrue(self.finding.manual_required)

    @override_settings(OPSPILOT_AUTOMATIC_REPAIR=True)
    def test_auto_enqueues_once_and_failed_verification_hands_off(self):
        run = self.start(mode='auto',trigger='both')
        self.complete(run)
        self.assertEqual(run.repair.task.status,'pending')
        task = run.repair.task
        task.status, task.finished_at = 'completed', timezone.now()-timedelta(minutes=3)
        task.save(update_fields=['status','finished_at'])
        dispatch()
        dispatch()
        run.refresh_from_db()
        self.assertEqual(run.status,'manual')
        self.assertEqual(self.finding.diagnostic_runs.filter(mode='repair').count(),1)
        self.finding.refresh_from_db()
        self.assertTrue(self.finding.manual_required)
        self.assertEqual(self.finding.notes.filter(text__contains='转人工').count(),1)

    def test_unexpected_or_malformed_evidence_never_repairs(self):
        run = self.start()
        self.complete(run,'[http] http · ok · HTTP 200\n诊断分支：service_down\n')
        self.assertEqual(run.status,'manual')
        self.assertFalse(run.branch)
        self.assertFalse(run.repair_id)

    def test_custom_revision_allows_only_paths_with_required_evidence(self):
        response = self.client.post('/mtask/diagnostic-trees',{'name':'自建示例','definition':DEFAULT_TREE},format='json')
        self.assertEqual(response.status_code,201,response.data)
        revisions = self.client.get('/mtask/diagnostic-trees').data['revisions']
        revision = next(item['id'] for item in revisions if item['tree_id']==response.data['id'])
        self.assertEqual(self.save(tree='custom',revision=revision,branch='healthy').status_code,400)
        run = self.start(tree='custom',revision=revision)
        self.complete(run)
        self.assertEqual(run.repair.task.status,'awaiting_approval')

    def test_scheduled_readonly_repeats_once_per_interval(self):
        run = self.start(mode='diagnose',trigger='scheduled',interval=20)
        self.complete(run)
        self.assertEqual(run.status,'diagnosed')
        previous = run.diagnosis_id
        dispatch()
        self.assertEqual(self.finding.diagnostic_runs.count(),1)
        future = timezone.now()+timedelta(minutes=21)
        with patch('mtask.diagnostic_workflows.timezone.now',return_value=future):
            dispatch()
            dispatch()
        run.refresh_from_db()
        self.assertEqual(DiagnosticWorkflowRun.objects.count(),1)
        self.assertEqual(self.finding.diagnostic_runs.count(),2)
        self.assertNotEqual(run.diagnosis_id,previous)
        self.assertEqual(run.status,'diagnosing')

    def test_overlapping_policies_require_human_decision(self):
        self.assertEqual(self.save(name='overlapping',trigger='scheduled').status_code,201)
        run = self.start()
        self.assertEqual(run.status,'manual')
        self.assertEqual(self.finding.diagnostic_runs.count(),0)
        self.finding.refresh_from_db()
        self.assertTrue(self.finding.manual_required)

    def test_approval_queue_and_alert_show_independent_authorization(self):
        run = self.start()
        self.complete(run)
        url = f'/mtask/incidents/{self.finding.pk}/runs?workspace=1'
        queue = '/mtask/repair-approvals'
        self.assertFalse(self.client.get(queue).data['results'][0]['can_approve'])
        self.assertFalse(self.client.get(url).data['pending_approval']['can_approve'])
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(queue).data['count'],0)
        self.assertEqual(self.client.get('/mtask/tasks?incident_mode=diagnosis&page=1').data['count'],0)
        self.client.force_authenticate(self.reviewer)
        entry = self.client.get(queue).data['results'][0]
        self.assertTrue(entry['can_approve'])
        self.assertEqual(entry['diagnosis_task_id'],run.diagnosis.task_id)
        self.assertTrue(self.client.get(url).data['pending_approval']['can_approve'])
        self.assertEqual(self.client.get('/mtask/tasks?incident_mode=diagnosis&page=1').data['count'],1)
        self.assertEqual(self.action(run).status_code,200)
        self.assertEqual(self.client.get(queue).data['count'],0)

    def test_readonly_policy_can_request_one_repair_with_independent_approval(self):
        run = self.start(mode='diagnose')
        self.complete(run)
        self.assertEqual(run.status,'diagnosed')
        WorkerLease.objects.create(name='default',heartbeat_at=timezone.now(),expires_at=timezone.now()+timedelta(minutes=1))
        url = f'/mtask/incidents/{self.finding.pk}/runs'
        self.assertTrue(self.client.get(url+'?workspace=1').data['repair']['available'])
        response = self.client.post(url,{'mode':'repair','confirmed':True},format='json')
        self.assertEqual(response.status_code,202,response.data)
        self.assertEqual(response.data['status'],'awaiting_approval')
        task_id = response.data['task_id']
        self.assertEqual(self.client.post(f'/mtask/tasks/{task_id}/action',{'action':'approve'}).status_code,403)
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.post(f'/mtask/tasks/{task_id}/action',{'action':'approve'}).status_code,200)

    def test_matching_one_click_result_is_reused_by_approval_policy(self):
        url = f'/mtask/incidents/{self.finding.pk}/runs'
        response = self.client.post(url,{'mode':'diagnosis','confirmed':True},format='json')
        self.assertEqual(response.status_code,202,response.data)
        existing = self.finding.diagnostic_runs.get()
        existing.task.status, existing.task.finished_at = 'completed', timezone.now()
        existing.task.save(update_fields=['status','finished_at'])
        existing.task.host_results.update(status='success',output=FAILED_NGINX)
        run = self.start()
        self.assertEqual(run.diagnosis_id,existing.pk)
        self.assertEqual(self.finding.diagnostic_runs.filter(mode='diagnosis').count(),1)
        dispatch()
        run.refresh_from_db()
        self.assertEqual(run.repair.task.status,'awaiting_approval')
