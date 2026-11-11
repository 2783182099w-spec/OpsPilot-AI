from datetime import timedelta
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from host.models import Finding, Host
from . import ssh_baseline
from .diagnostics import queue_diagnosis, recommended_tree
from .models import DiagnosticWorkflowRun, ExecutionTask, WorkerLease
from .diagnostic_workflows import dispatch

with patch.dict(sys.modules,{'fcntl':Mock()}):
    from . import ssh_baseline_script


class BaselineApprovalTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_superuser('baseline-owner')
        self.reviewer = users.create_superuser('baseline-reviewer')
        self.host = Host.objects.create(name='fixture',ip_addr='192.0.2.8',username='root',port=22,environment='test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        now = timezone.now()
        WorkerLease.objects.create(name='default',heartbeat_at=now,expires_at=now+timedelta(minutes=1))

    def confirmed_finding(self, check='ssh_password', observed='passwordauthentication yes', age=0):
        finding = Finding.objects.create(owner=self.owner,host=self.host,source='inspection',title='SSH fixture',
            condition='unknown',signal={'check_id':check},dedupe_key=f'{self.owner.pk}:{self.host.pk}:{check}')
        run = queue_diagnosis(finding,recommended_tree(finding),self.owner)
        run.task.status = 'completed'
        run.task.finished_at = timezone.now()-timedelta(minutes=age)
        run.task.save(update_fields=['status','finished_at'])
        run.task.host_results.update_or_create(host_id_snapshot=self.host.pk,defaults={'status':'success','checks':[
            {'id':check,'title':'SSH fixture','state':'warning','summary':'SSH 策略异常','evidence':observed},
        ]})
        return finding

    def test_direct_baseline_approval_is_independent_and_action_specific(self):
        finding = self.confirmed_finding()
        url = f'/mtask/incidents/{finding.pk}/runs'
        self.assertTrue(self.client.get(url+'?workspace=1').data['repair']['available'])
        self.assertEqual(self.client.post(url,{'confirmed':True,'mode':'repair_once'},format='json').status_code,400)
        response = self.client.post(url,{'confirmed':True,'mode':'repair'},format='json')
        self.assertEqual(response.status_code,202,response.data)
        task = f'/mtask/tasks/{response.data["task_id"]}/action'
        self.assertEqual(self.client.post(task,{'action':'approve'}).status_code,403)
        self.client.force_authenticate(self.reviewer)
        pending = self.client.get('/mtask/repair-approvals').data['results'][0]
        self.assertEqual(pending['action']['name'],'禁用 SSH 密码登录')
        self.assertTrue(pending['can_approve'])
        self.assertEqual(self.client.post(task,{'action':'approve'}).data['status'],'pending')

    def test_root_action_and_production_restriction(self):
        finding = self.confirmed_finding('ssh_root','permitrootlogin yes')
        url = f'/mtask/incidents/{finding.pk}/runs'
        self.assertTrue(self.client.get(url+'?workspace=1').data['repair']['available'])
        self.host.environment = 'production'
        self.host.save(update_fields=['environment'])
        self.assertEqual(self.client.post(url,{'confirmed':True,'mode':'repair'},format='json').status_code,400)

    def test_stale_or_changed_evidence_is_rejected(self):
        finding = self.confirmed_finding(age=16)
        url = f'/mtask/incidents/{finding.pk}/runs'
        self.assertEqual(self.client.post(url,{'confirmed':True,'mode':'repair'},format='json').status_code,400)
        run = finding.diagnostic_runs.get()
        run.task.finished_at = timezone.now()
        run.task.save(update_fields=['finished_at'])
        response = self.client.post(url,{'confirmed':True,'mode':'repair'},format='json')
        self.assertEqual(response.status_code,202,response.data)
        finding.state = 'resolved'
        finding.save(update_fields=['state'])
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.post(f'/mtask/tasks/{response.data["task_id"]}/action',
                                         {'action':'approve'}).status_code,400)


    def test_expired_approval_allows_renewal_before_any_dispatch(self):
        finding = self.confirmed_finding()
        url = f'/mtask/incidents/{finding.pk}/runs'
        first = self.client.post(url,{'confirmed':True,'mode':'repair'},format='json')
        self.assertEqual(first.status_code,202)
        diagnosis = finding.diagnostic_runs.get(mode='diagnosis').task
        diagnosis.finished_at = timezone.now()-timedelta(minutes=16)
        diagnosis.save(update_fields=['finished_at'])
        dispatch()
        self.assertEqual(ExecutionTask.objects.get(pk=first.data['task_id']).status,'cancelled')
        diagnosis.finished_at = timezone.now()
        diagnosis.save(update_fields=['finished_at'])
        self.assertEqual(self.client.post(url,{'confirmed':True,'mode':'repair'},format='json').status_code,202)

    def test_policy_generates_independent_approval_and_can_be_disabled(self):
        finding = self.confirmed_finding()
        finding.condition = 'active'
        finding.save(update_fields=['condition'])
        policy = {'name':'SSH policy','enabled':True,'source':'inspection','host_ids':[self.host.pk],
                  'signal':'ssh_password','tree':'source','mode':'approval','trigger':'event','branch':'confirmed_violation'}
        self.assertEqual(self.client.post('/mtask/diagnostic-policies',{**policy,'mode':'auto'},format='json').status_code,400)
        response = self.client.post('/mtask/diagnostic-policies',policy,format='json')
        self.assertEqual(response.status_code,201,response.data)
        dispatch()
        dispatch()
        workflow = DiagnosticWorkflowRun.objects.get(finding=finding)
        self.assertEqual(workflow.branch,'confirmed_violation')
        self.assertEqual(workflow.repair.task.status,'awaiting_approval')
        self.client.post(f'/mtask/diagnostic-policies/{workflow.policy_id}',
            {'version':1,'name':'SSH policy','enabled':False,'disable_only':True},format='json')
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.post(f'/mtask/tasks/{workflow.repair.task_id}/action',{'action':'approve'}).status_code,400)


class BaselineExecutionTests(SimpleTestCase):
    def test_managed_directives_are_idempotent_and_match_is_refused(self):
        script = ssh_baseline_script
        original = b'PasswordAuthentication yes\nPubkeyAuthentication yes\n'
        updated = script.candidate(original,script.SETTINGS['ssh_password'])
        self.assertEqual(script.candidate(updated,script.SETTINGS['ssh_password']),updated)
        self.assertIn(b'passwordauthentication no\n',updated)
        self.assertIn(b'kbdinteractiveauthentication no\n',updated)
        with self.assertRaisesRegex(RuntimeError,'Match'):
            script.verify_layout(b'Match User root\nPasswordAuthentication yes\n')

    def test_timer_precedes_config_write_and_rollback_restores_backup(self):
        script = ssh_baseline_script
        original = b'PasswordAuthentication yes\n'
        observed = []
        token = 'a'*32
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'sshd_config'
            config.write_bytes(original)
            active = Path(folder)/'active'
            work = Path(folder)/token

            def command(*args):
                observed.append(args[:2])
                return 'active'

            def effective(path):
                return {'pubkeyauthentication':'yes','permitrootlogin':'yes',
                        'passwordauthentication':'no' if b'passwordauthentication no' in path.read_bytes() else 'yes',
                        'kbdinteractiveauthentication':'no'}

            def replace_config(content, verify=None):
                staged = Path(folder)/'candidate'
                staged.write_bytes(content)
                if verify:
                    verify(staged)
                observed.append(('replace',))
                config.write_bytes(content)

            with patch.multiple(script,CONFIG=config,read_config=lambda path:path.read_bytes(),safe_directory=Mock(),
                                unit_name=lambda:'sshd.service',command=command,effective=effective,
                                replace_config=replace_config):
                script.apply('ssh_password',token,active,work)
                self.assertEqual(observed[0],('systemd-run','--unit=opspilot-sshd-'+token))
                self.assertEqual(observed[1],('systemctl','is-active'))
                self.assertEqual(observed[2],('replace',))
                self.assertNotEqual(config.read_bytes(),original)
                script.rollback(active,work)
                self.assertEqual(config.read_bytes(),original)
                self.assertFalse(active.exists())

    def test_failed_fresh_key_session_requests_rollback(self):
        host = SimpleNamespace(ip_addr='192.0.2.8',port=22,username='root')
        original = Mock()
        original.execute_cmd.return_value = (0,'SSH 基线变更已回退')
        with patch('mtask.ssh_baseline.SSHParamiko') as connector:
            connector.return_value.get_connected_client.return_value = None
            stop = Mock()
            stop.is_set.return_value = False
            success, output = ssh_baseline.verify_fresh_session(
                host,'test-key',{'rollback_token':'a'*32},original,stop,Mock(),
            )
        self.assertFalse(success)
        self.assertIn('已回退',output)
        self.assertTrue(original.execute_cmd.call_args.args[0].endswith(' rollback'))
