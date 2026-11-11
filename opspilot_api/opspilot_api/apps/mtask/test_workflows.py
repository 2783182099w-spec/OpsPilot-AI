from datetime import timedelta
import io
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from host.models import Finding, Host
from monitoring.models import ServiceCheck
from monitoring.service_checks import apply_sample
from .diagnostics import DEFAULT_TREE, escalate_repair, tree_definition, verify_incident
from .models import DiagnosticRun, DiagnosticTree, ExecutionTask, ManagedJob, ReleaseRun, WorkerLease
from .release_script import fetch

User = get_user_model()


class WorkflowsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('release-requester')
        self.reviewer = User.objects.create_superuser('release-reviewer')
        self.operator = User.objects.create_user('workflow-operator')
        self.host = Host.objects.create(name='workflow-fixture', ip_addr='192.0.2.8', port=22,
                                        username='ops', environment='test')
        self.host.users.add(self.operator)
        self.other_host = Host.objects.create(name='other-fixture', ip_addr='192.0.2.9', port=22,
                                              username='ops', environment='test')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.targets = {'web':{'unit':'demo.service','host_ids':[self.host.pk],
                               'origin':'https://artifacts.example.test','environment':'test',
                               'port':8080,'path':'/health'}}
        self.manifest = {'application':'web','host_ids':[self.host.pk],'version':'1.0.0',
                         'url':'https://artifacts.example.test/web', 'sha256':'a'*64,'reason':'fixture only'}

    def release(self, body):
        preview = self.client.post('/mtask/releases', body, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        return self.client.post('/mtask/releases', {**body,'token':preview.data['token'],'confirmed':True},format='json')

    def test_release_manifest_signed_and_independently_approved(self):
        with override_settings(OPSPILOT_RELEASE_TARGETS=self.targets):
            for url in ['http://artifacts.example.test/web','https://other.example.test/web',
                        'https://artifacts.example.test/web?credential=fixture','https://artifacts.example.test/a/../web']:
                self.assertEqual(self.client.post('/mtask/releases',{**self.manifest,'url':url},format='json').status_code,400)
            self.assertEqual(self.client.post('/mtask/releases',{**self.manifest,'host_ids':[self.other_host.id]},format='json').status_code,400)
            preview = self.client.post('/mtask/releases',self.manifest,format='json')
            self.assertEqual(preview.status_code,200)
            self.assertFalse(ReleaseRun.objects.exists())
            altered = {**self.manifest,'version':'2','token':preview.data['token'],'confirmed':True}
            self.assertEqual(self.client.post('/mtask/releases',altered,format='json').status_code,400)
            submitted = self.release(self.manifest)
            self.assertEqual(submitted.status_code,202,submitted.data)
            task_id = submitted.data['task_id']
            self.assertEqual(self.release(self.manifest).status_code,400)
            action = f'/mtask/tasks/{task_id}/action'
            self.assertEqual(self.client.post(action,{'action':'approve'}).status_code,403)
            self.client.force_authenticate(self.reviewer)
            self.assertEqual(self.client.post(action,{'action':'approve'}).data['status'],'pending')

    def test_rollback_requires_version_verified_on_host(self):
        with override_settings(OPSPILOT_RELEASE_TARGETS=self.targets):
            older = self.release(self.manifest)
            old = ReleaseRun.objects.get(pk=older.data['id'])
            ExecutionTask.objects.filter(pk=old.task_id).update(status='completed')
            old.task.host_results.update(status='success',may_have_run=True)
            newer = self.release({**self.manifest,'version':'2.0.0','sha256':'b'*64})
            current = ReleaseRun.objects.get(pk=newer.data['id'])
            ExecutionTask.objects.filter(pk=current.task_id).update(status='failed')
            request = {'application':'web','host_ids':[self.host.pk],'reason':'rollback fixture',
                       'rollback_of':current.pk,'restore_from':old.pk}
            self.assertEqual(self.client.post('/mtask/releases',request,format='json').status_code,400)
            current.task.host_results.update(status='failed',may_have_run=True)
            restored = self.release(request)
            self.assertEqual(restored.status_code,202,restored.data)
            self.assertEqual(restored.data['version'],'1.0.0')
            self.assertEqual(restored.data['rollback_of'],current.pk)

    def test_release_history_detail_permissions_and_version_binding(self):
        with override_settings(OPSPILOT_RELEASE_TARGETS=self.targets):
            submission = self.release(self.manifest)
            self.assertEqual(submission.status_code,202)
            pk = submission.data['id']
            detail_url = f'/mtask/releases/{pk}'
            detail = self.client.get(detail_url).data
            self.assertEqual(detail['hosts']['count'],1)
            self.assertFalse(detail['actions']['approve'])
            self.assertTrue(detail['actions']['cancel'])
            listing = self.client.get('/mtask/releases',{'page':1,'search':'1.0','status':'awaiting_approval'})
            self.assertEqual(listing.data['count'],1)
            self.assertEqual(listing.data['overview']['awaiting_approval'],1)
            self.assertEqual(self.client.post('/mtask/releases',{**self.manifest,'sha256':'b'*64},format='json').status_code,400)
            self.client.force_authenticate(self.reviewer)
            self.assertTrue(self.client.get(detail_url).data['actions']['approve'])
            self.client.force_authenticate(self.operator)
            self.assertEqual(self.client.get(detail_url).status_code,404)
            self.assertEqual(self.client.get(detail_url+'/rollback-options').status_code,403)

    def test_rollback_candidates_exclude_superseded_or_retired_targets(self):
        self.targets['web']['host_ids'].append(self.other_host.pk)
        body = {**self.manifest,'host_ids':[self.host.pk,self.other_host.pk]}
        with override_settings(OPSPILOT_RELEASE_TARGETS=self.targets):
            first = self.release(body).data
            original = ReleaseRun.objects.get(pk=first['id'])
            original.task.host_results.update(status='success',may_have_run=True)
            ExecutionTask.objects.filter(pk=original.task_id).update(status='completed')
            current = self.release({**body,'version':'2.0','sha256':'b'*64}).data
            newer = ReleaseRun.objects.get(pk=current['id'])
            newer.task.host_results.update(status='failed',may_have_run=True)
            ExecutionTask.objects.filter(pk=newer.task_id).update(status='failed')
            url = f'/mtask/releases/{newer.pk}/rollback-options'
            self.assertEqual(set(self.client.get(url).data['candidates'][0]['host_ids']),set(body['host_ids']))
            later = self.release({**self.manifest,'version':'3.0','sha256':'c'*64}).data
            self.assertTrue(self.client.get(url).data['busy'])
            self.assertEqual(self.client.get(url).data['candidates'][0]['host_ids'],[self.other_host.pk])
            self.assertEqual(self.client.get(f"/mtask/releases/{later['id']}/rollback-options").data['candidates'],[])
            Host.objects.filter(pk=self.other_host.pk).update(asset_status='retired')
            self.assertEqual(self.client.get(url).data['candidates'],[])

    def test_approval_checks_current_release_policy(self):
        with override_settings(OPSPILOT_RELEASE_TARGETS=self.targets):
            submission = self.release(self.manifest)
            self.targets['web']['path'] = '/changed'
            self.client.force_authenticate(self.reviewer)
            response = self.client.post(f"/mtask/tasks/{submission.data['task_id']}/action",{'action':'approve'})
            self.assertEqual(response.status_code,400)
            self.assertEqual(ExecutionTask.objects.get(pk=submission.data['task_id']).status,'awaiting_approval')

    def test_tree_validation_and_host_scope(self):
        invalid = {**DEFAULT_TREE,'nodes':[dict(node) for node in DEFAULT_TREE['nodes']]}
        invalid['nodes'][0]['ok'] = invalid['root']
        with self.assertRaises(ValidationError):
            tree_definition(invalid)
        tree = DiagnosticTree.objects.create(name='fixture tree',definition=tree_definition(DEFAULT_TREE))
        self.client.force_authenticate(self.operator)
        url = f'/mtask/diagnostic-trees/{tree.pk}/trial'
        denied = self.client.post(url,{'host_id':self.other_host.pk,'version':1,'confirmed':True},format='json')
        self.assertEqual(denied.status_code,403)
        result = self.client.post(url,{'host_id':self.host.pk,'version':1,'confirmed':True},format='json')
        self.assertEqual(result.status_code,202,result.data)
        self.assertEqual(result.data['incident']['tree_version'],1)
        self.assertEqual(self.client.post(url,{'host_id':self.host.pk,'version':2,'confirmed':True},format='json').status_code,400)

    def make_probe(self, mode):
        return ServiceCheck.objects.create(host=self.host,owner=self.admin,name='fixture HTTP',mode=mode,
            unit='nginx.service',port=8080,path='/health',enabled=True,
            tree=DiagnosticTree.objects.create(name='fixture',definition=DEFAULT_TREE))

    def sample(self, probe, ok):
        apply_sample(probe.pk,probe.version,self.host.ip_addr,ok,'HTTP 200' if ok else 'fixture failed')

    def test_monitor_thresholds_occurrence_and_manual_workflow(self):
        probe = self.make_probe('approval')
        for _ in range(2):
            self.sample(probe,False)
        self.assertFalse(Finding.objects.exists())
        self.sample(probe,False)
        finding = Finding.objects.get(source='service')
        path = f'/mtask/incidents/{finding.pk}/runs'
        self.assertEqual(self.client.post(path,{'mode':'repair','confirmed':True},format='json').status_code,400)
        diagnosis = DiagnosticRun.objects.get(mode='diagnosis')
        ExecutionTask.objects.filter(pk=diagnosis.task_id).update(status='completed')
        Finding.objects.filter(pk=finding.pk).update(silenced_until=timezone.now()+timedelta(minutes=10))
        self.assertEqual(self.client.post(path,{'mode':'repair','confirmed':True},format='json').status_code,400)
        Finding.objects.filter(pk=finding.pk).update(silenced_until=None)
        repair = self.client.post(path,{'mode':'repair','confirmed':True},format='json')
        self.assertEqual(repair.status_code,202,repair.data)
        self.assertEqual(repair.data['status'],'awaiting_approval')
        self.assertEqual(self.client.post(path,{'mode':'repair','confirmed':True},format='json').status_code,400)
        for _ in range(3):
            self.sample(probe,True)
        finding.refresh_from_db()
        self.assertEqual(finding.condition,'recovered')
        with self.assertRaises(ValidationError):
            verify_incident(ExecutionTask.objects.get(pk=repair.data['task_id']))
        for _ in range(3):
            self.sample(probe,False)
        finding.refresh_from_db()
        self.assertEqual(finding.occurrences,2)

    def test_auto_repair_opt_in_and_kill_switch(self):
        probe = self.make_probe('auto')
        with override_settings(OPSPILOT_AUTOMATIC_REPAIR=False):
            for _ in range(3):
                self.sample(probe,False)
            job = DiagnosticRun.objects.get(mode='diagnosis')
            ExecutionTask.objects.filter(pk=job.task_id).update(status='completed')
            job.task.host_results.update(output='[http] http · failed · HTTP 503\n诊断结论：服务不可用')
            self.sample(probe,False)
            self.assertFalse(DiagnosticRun.objects.filter(mode='repair').exists())
        with override_settings(OPSPILOT_AUTOMATIC_REPAIR=True):
            self.sample(probe,False)
            self.sample(probe,False)
            run = DiagnosticRun.objects.get(mode='repair')
            self.assertEqual(run.task.status,'pending')
            self.assertTrue(run.task.options['incident']['automatic'])
            verify_incident(run.task)
            ServiceCheck.objects.filter(pk=probe.pk).update(enabled=False)
            with self.assertRaises(ValidationError):
                verify_incident(run.task)

    @override_settings(OPSPILOT_AUTOMATIC_REPAIR=True)
    def test_once_is_current_occurrence_only_and_escalation_is_idempotent(self):
        probe = self.make_probe('approval')
        for _ in range(3):
            self.sample(probe,False)
        finding = Finding.objects.get(source='service')
        path = f'/mtask/incidents/{finding.pk}/runs'
        data = {'mode':'repair_once','confirmed':True,'escalate_on_failure':True}
        self.assertEqual(self.client.post(path,data,format='json').status_code,400)
        diagnosis = DiagnosticRun.objects.get(mode='diagnosis')
        ExecutionTask.objects.filter(pk=diagnosis.task_id).update(status='completed')
        self.assertEqual(self.client.post(path,data,format='json').status_code,400)
        diagnosis.task.host_results.update(output='[http] http · failed · HTTP 503\n诊断结论：服务不可用')
        result = self.client.post(path,data,format='json')
        self.assertEqual(result.status_code,202,result.data)
        run = DiagnosticRun.objects.get(mode='repair')
        self.assertEqual(run.task.options['incident']['automation_scope'],'once')
        self.assertEqual(run.task.status,'pending')
        verify_incident(run.task)
        self.assertEqual(self.client.post(path,data,format='json').status_code,400)
        escalate_repair(run.task,'fixture failed')
        escalate_repair(run.task,'repeat')
        finding.refresh_from_db()
        self.assertTrue(finding.manual_required)
        self.assertEqual(finding.notes.filter(text__contains='转人工').count(),1)
        self.assertEqual(self.client.get('/host/findings',{'page':1,'manual':1}).data['count'],1)
        self.assertEqual(self.client.patch(f'/host/findings/{finding.pk}',{'ack_follow_up':True},format='json').status_code,200)
        finding.refresh_from_db()
        self.assertFalse(finding.manual_required)
        self.assertEqual(finding.assignee,self.admin.username)
        probe.refresh_from_db()
        self.assertEqual(probe.mode,'approval')

    @override_settings(OPSPILOT_AUTOMATIC_REPAIR=True)
    def test_completed_repair_without_external_recovery_escalates(self):
        probe = self.make_probe('auto')
        for _ in range(3):
            self.sample(probe,False)
        diagnosis = DiagnosticRun.objects.get(mode='diagnosis')
        ExecutionTask.objects.filter(pk=diagnosis.task_id).update(status='completed')
        diagnosis.task.host_results.update(output='[http] http · failed · HTTP 503\n诊断结论：服务不可用')
        self.sample(probe,False)
        repair = DiagnosticRun.objects.get(mode='repair')
        ExecutionTask.objects.filter(pk=repair.task_id).update(status='completed',
            finished_at=timezone.now()-timedelta(seconds=121))
        self.sample(probe,False)
        self.assertTrue(Finding.objects.get(pk=repair.finding_id).manual_required)
        self.assertTrue(repair.finding.notes.filter(text__contains='外部 HTTP').exists())

    @override_settings(OPSPILOT_AUTOMATIC_REPAIR=True)
    def test_policy_can_skip_failure_escalation(self):
        probe = self.make_probe('auto')
        ServiceCheck.objects.filter(pk=probe.pk).update(escalate_on_failure=False)
        for _ in range(3):
            self.sample(probe,False)
        diagnosis = DiagnosticRun.objects.get(mode='diagnosis')
        ExecutionTask.objects.filter(pk=diagnosis.task_id).update(status='completed')
        diagnosis.task.host_results.update(output='[http] http · failed · HTTP 503\n诊断结论：服务不可用')
        self.sample(probe,False)
        run = DiagnosticRun.objects.get(mode='repair')
        self.assertEqual(run.task.options['incident']['automation_scope'],'policy')
        escalate_repair(run.task,'fixture failed')
        self.assertFalse(Finding.objects.get(pk=run.finding_id).manual_required)

    def test_recommended_diagnosis_without_template_and_scoped_evidence(self):
        probe = ServiceCheck.objects.create(host=self.host,owner=self.admin,name='fixture HTTP',
            mode='monitor',unit='nginx.service',port=8080,path='/ready',enabled=True)
        for _ in range(3):
            self.sample(probe,False)
        finding = Finding.objects.get(source='service')
        now = timezone.now()
        WorkerLease.objects.create(name='default',heartbeat_at=now,expires_at=now+timedelta(seconds=30))
        url = f'/mtask/incidents/{finding.pk}/runs'
        started = self.client.post(url,{'confirmed':True},format='json')
        self.assertEqual(started.status_code,202,started.data)
        run = DiagnosticRun.objects.get()
        self.assertIn('Nginx',run.task.options['incident']['tree_name'])
        self.assertEqual(run.task.options['incident']['tree']['nodes'][0]['port'],8080)
        run.task.host_results.update(output='[http] http · failed · HTTP 503\n诊断结论：服务异常')
        ExecutionTask.objects.filter(pk=run.task_id).update(status='completed')
        self.client.force_authenticate(self.operator)
        result = self.client.get(url,{'workspace':1})
        self.assertTrue(result.data['worker_online'])
        self.assertEqual(result.data['runs'][0]['checks'][0]['evidence'],'HTTP 503')
        self.assertEqual(result.data['runs'][0]['summary']['outcome'],'abnormal')
        self.assertIn('上游',result.data['runs'][0]['summary']['next_step'])
        self.assertFalse(result.data['runs'][0]['can_open_task'])
        self.assertEqual(self.client.get(f'/mtask/tasks/{run.task_id}').status_code,404)
        self.assertEqual(self.client.get(url).data,[])
        self.host.users.remove(self.operator)
        self.assertEqual(self.client.get(url,{'workspace':1}).status_code,404)

    def test_alert_overview_counts_and_prioritizes_visible_queue(self):
        now = timezone.now()
        for key, severity, state, condition in [
                ('serious','critical','open','active'), ('warning','warning','processing','active'),
                ('closed','warning','resolved','active'), ('restored','critical','open','recovered')]:
            Finding.objects.create(host=self.host,owner=self.admin,source='monitor',dedupe_key=key,
                                   title=key,severity=severity,state=state,condition=condition,
                                   recovered_at=now if condition=='recovered' else None)
        Finding.objects.create(host=self.other_host,owner=self.admin,source='monitor',dedupe_key='out-of-scope',
                               title='other',severity='critical',condition='active')
        self.client.force_authenticate(self.operator)
        result = self.client.get('/host/findings',{'bucket':'inbox','overview':1,'sort':'priority','page':1})
        self.assertEqual(result.data['overview'],{'inbox':2,'critical':1,'active':3,'recovered':1})
        self.assertEqual([row['title'] for row in result.data['results']],['serious','warning'])

    def test_seed_jobs_is_idempotent(self):
        for _ in range(2):
            call_command('seed_ops_jobs',owner=self.admin.username,stdout=io.StringIO())
        self.assertEqual(ManagedJob.objects.filter(state='published').count(),4)
        for row in ManagedJob.objects.all():
            self.assertEqual(row.definition['risk'],'read_only')
            self.assertNotIn('production',row.definition['environments'])
            self.assertEqual(row.revisions.count(),1)

    @patch('mtask.release_script.http.client.HTTPSConnection')
    def test_remote_artifact_digest_and_redirect_rejected(self, connection):
        response = connection.return_value.getresponse.return_value.__enter__.return_value
        response.status = 302
        with self.assertRaisesRegex(ValueError,'without redirects'):
            fetch('https://artifacts.example.test/web',io.BytesIO(),'a'*64)
        response.status = 200
        response.read = Mock(side_effect=[b'demo artifact',b''])
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            fetch('https://artifacts.example.test/web',io.BytesIO(),'a'*64)
