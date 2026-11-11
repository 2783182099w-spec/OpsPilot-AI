import shlex
import time
from datetime import timedelta
from threading import Lock
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from host.models import Host
from mtask.models import ExecutionTask, HostResult, WorkerLease, TaskSchedule, CmdTemplate, CmdTemplateCategory
from mtask.engine import acquire_lease, release_lease, dispatch_schedules
from mtask.tasks import run_task
from mtask.inspection import assess, parse, validate_options
from mtask.template_tools import render


class OperationsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ops-user')
        self.host = Host.objects.create(name='offline-test', ip_addr='127.0.0.1', port=22222, username='test')
        self.host.users.add(self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_readonly_task_is_queued_but_unknown_profile_rejected(self):
        response = self.client.post('/mtask/tasks', {'kind':'inspection','host_ids':[self.host.id],'confirmed':True}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(self.client.post('/mtask/tasks', {'kind':'inspection','host_ids':[self.host.id],'confirmed':True,'options':{'services':['nginx; shutdown']}}, format='json').status_code, 400)

    def test_cancel_pending_and_retry_require_confirmation(self):
        task = ExecutionTask.objects.create(owner=self.user, command='id', host_ids=[self.host.id])
        self.assertEqual(self.client.post(f'/mtask/tasks/{task.id}/action', {'action':'cancel'}, format='json').status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')
        self.assertEqual(self.client.post(f'/mtask/tasks/{task.id}/action', {'action':'retry'}, format='json').status_code, 400)

    def test_live_lease_never_interrupts_existing_task(self):
        token = acquire_lease()
        task = ExecutionTask.objects.create(owner=self.user, command='id', host_ids=[self.host.id], status='running', worker_token=token)
        self.assertIsNone(acquire_lease())
        task.refresh_from_db()
        self.assertEqual(task.status, 'running')
        release_lease(token)
        new = acquire_lease()
        self.assertNotEqual(new, token)
        task.refresh_from_db()
        self.assertEqual(task.status, 'interrupted')

    def test_schedule_is_disabled_by_default_and_revocation_disables_it(self):
        response = self.client.post('/mtask/schedules', {'name':'test','host_ids':[self.host.id],'interval_minutes':15}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data['enabled'])
        row = TaskSchedule.objects.get(pk=response.data['id'])
        row.next_run = timezone.now()-timedelta(seconds=1)
        row.save()
        dispatch_schedules()
        self.assertEqual(ExecutionTask.objects.count(), 0)
        row.enabled = True
        row.save()
        self.host.users.clear()
        dispatch_schedules()
        row.refresh_from_db()
        self.assertFalse(row.enabled)

    def test_schedule_does_not_overlap(self):
        task=ExecutionTask.objects.create(owner=self.user, kind='inspection', command='', host_ids=[self.host.id])
        TaskSchedule.objects.create(owner=self.user,name='test',host_ids=[self.host.id],kind='inspection',options={},interval_minutes=15,enabled=True,next_run=timezone.now()-timedelta(seconds=1),last_task=task)
        dispatch_schedules()
        self.assertEqual(ExecutionTask.objects.count(),1)

    def test_unknown_results_are_not_reported_as_pass(self):
        checks=parse('',validate_options('inspection',{'profile':'baseline'}))
        self.assertTrue(all(check['state']=='unknown' for check in checks))
        self.assertEqual(assess('patches','No updates available',0)[0],'review')
        self.assertEqual(assess('disk','/dev/x 100 99 1 99% /',0)[0],'warning')
        self.assertEqual(assess('memory','MemTotal: 1000\nMemAvailable: 50',0)[0],'warning')
        self.assertEqual(assess('cpu','1 0 1 8 0 0 0 0\n2 0 2 16 0 0 0 0',0)[0],'pass')

    @patch('mtask.tasks.PkeyManager.get',return_value=('fake','fake'))
    @patch('mtask.tasks.SSHParamiko')
    def test_cancel_before_dispatch_never_connects(self, ssh, key):
        task=ExecutionTask.objects.create(owner=self.user,host_ids=[self.host.id],command='id',cancel_requested=True)
        run_task(task)
        ssh.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status,'cancelled')

    def test_template_quotes_parameter_and_version_is_enforced(self):
        category=CmdTemplateCategory.objects.create(name='test')
        template=CmdTemplate.objects.create(name='test',category=category,cmd='printf %s {{value}}',parameters=[{'name':'value'}])
        value='x; touch /tmp/should-not-exist'
        command=render(template,{'value':value})
        self.assertEqual(shlex.split(command)[-1],value)
        self.assertEqual(self.client.post('/mtask/tasks',{'host_ids':[self.host.id],'template_id':template.id,'template_version':999,'parameters':{'value':value},'confirmed':True},format='json').status_code,400)
        template.cmd='echo "{{value}}"'
        with self.assertRaises(ValidationError):
            render(template,{'value':value})

    def test_result_pagination_and_safe_csv_export(self):
        task=ExecutionTask.objects.create(owner=self.user,host_ids=list(range(1,42)),command='id',status='completed',completed_hosts=41)
        HostResult.objects.bulk_create([HostResult(task=task,host_id_snapshot=i,host_info={'id':i,'name':'=formula','ip_addr':'127.0.0.1'},status='success',output='mock only') for i in range(1,42)])
        response=self.client.get(f'/mtask/tasks/{task.id}?results_page=2')
        self.assertEqual(len(response.data['results']),20)
        self.assertEqual(response.data['results_count'],41)
        report=self.client.get(f'/mtask/tasks/{task.id}/report')
        self.assertEqual(report.status_code,200)
        content=b''.join(report.streaming_content).decode('utf-8-sig')
        self.assertIn("'=formula",content)
        report.close()

    @patch('mtask.tasks.PkeyManager.get',return_value=('fake','fake'))
    def test_300_mock_hosts_never_exceed_four_connections(self, key):
        hosts=[Host(name=f'mock-{i}',ip_addr='127.0.0.1',port=22222,username='test') for i in range(299)]
        Host.objects.bulk_create(hosts)
        Host.users.through.objects.bulk_create([Host.users.through(host_id=h.id,user_id=self.user.id) for h in hosts])
        ids=[self.host.id]+[h.id for h in hosts]
        task=ExecutionTask.objects.create(owner=self.user,host_ids=ids,command='id')
        lock=Lock()
        counters={'active':0,'peak':0}
        class FakeSSH:
            def __init__(self,*args,**kwargs): pass
            def get_connected_client(self): return True
            def execute_cmd(self,*args,**kwargs):
                with lock:
                    counters['active']+=1
                    counters['peak']=max(counters['peak'],counters['active'])
                time.sleep(.003)
                with lock: counters['active']-=1
                return 0,'mock-only, no network'
            def close(self): pass
        with patch('mtask.tasks.SSHParamiko',FakeSSH):
            run_task(task)
        task.refresh_from_db()
        self.assertEqual(task.status,'completed')
        self.assertEqual(task.completed_hosts,300)
        self.assertEqual(HostResult.objects.filter(task=task,status='success').count(),300)
        self.assertLessEqual(counters['peak'],4)
        self.assertGreater(counters['peak'],1)


    def test_task_filters_pagination_and_result_check_summary(self):
        task = ExecutionTask.objects.create(owner=self.user, kind='inspection', command='', host_ids=[self.host.id], status='completed')
        HostResult.objects.create(task=task, host_id_snapshot=self.host.id, host_info={'name':'offline-test','ip_addr':'127.0.0.1'}, status='success', checks=[{'id':'disk','title':'disk','state':'warning','summary':'high','evidence':'90%'}])
        response = self.client.get('/mtask/tasks?page=1&kind=inspection&status=completed')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(self.client.get('/mtask/tasks?page=1&kind=command').data['count'], 0)
        self.assertEqual(self.client.get('/mtask/tasks?page=1&date_from=invalid').status_code, 400)
        detail = self.client.get(f'/mtask/tasks/{task.id}?host_search=offline&check_state=warning').data
        self.assertEqual(detail['results_count'], 1)
        self.assertEqual(detail['check_summary'], {'warning':1})
        detail = self.client.get(f'/mtask/tasks/{task.id}?check_state=pass').data
        self.assertEqual(detail['results'], [])
        self.assertEqual(detail['check_summary'], {'warning':1})

    def test_schedule_edit_run_now_and_authorization(self):
        response = self.client.post('/mtask/schedules', {'name':'editable','host_ids':[self.host.id],'interval_minutes':60}, format='json')
        pk = response.data['id']
        future_value = timezone.now()+timedelta(hours=2)
        future = future_value.astimezone().isoformat()
        response = self.client.patch(f'/mtask/schedules/{pk}', {'name':'renamed','kind':'probe','interval_minutes':30,'next_run':future}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'renamed')
        self.assertEqual(response.data['kind'], 'probe')
        self.assertFalse(response.data['enabled'])
        self.assertEqual(self.client.post(f'/mtask/schedules/{pk}', {}, format='json').status_code, 400)
        response = self.client.post(f'/mtask/schedules/{pk}', {'confirmed':True}, format='json')
        self.assertEqual(response.status_code, 202)
        task = ExecutionTask.objects.get(pk=response.data['id'])
        self.assertEqual(task.options['schedule_id'], pk)
        self.assertEqual(self.client.post(f'/mtask/schedules/{pk}', {'confirmed':True}, format='json').status_code, 400)
        row = TaskSchedule.objects.get(pk=pk)
        self.assertEqual(row.next_run, future_value)
        self.assertFalse(row.enabled)
        self.assertEqual(self.client.patch(f'/mtask/schedules/{pk}', {'enabled':True}, format='json').status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.next_run, future_value)
        self.host.users.clear()
        self.assertIn(self.client.patch(f'/mtask/schedules/{pk}', {'enabled':True}, format='json').status_code, (403,404))

    def test_invalid_first_execution_does_not_create_partial_schedule(self):
        response = self.client.post('/mtask/schedules', {'name':'invalid','host_ids':[self.host.id],'next_run':'invalid'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TaskSchedule.objects.count(), 0)

    def test_findings_link_source_and_reopen_after_recurrence(self):
        from host.models import Finding
        from mtask.tasks import record_findings
        task = ExecutionTask.objects.create(owner=self.user,kind='inspection',host_ids=[self.host.id],command='')
        check = {'id':'disk','title':'Disk','state':'warning','summary':'high','evidence':'90%'}
        record_findings(task, self.host.id, [check])
        row = Finding.objects.get()
        self.assertEqual(row.source_task_id, task.id)
        record_findings(task, self.host.id, [{**check, 'state':'pass'}])
        row.refresh_from_db()
        self.assertEqual(row.state, 'resolved')
        record_findings(task, self.host.id, [check])
        row.refresh_from_db()
        self.assertEqual(row.state, 'open')


    def test_template_editor_validates_placeholder_definitions_before_save(self):
        from mtask.serializers import CmdTemplateSerializer
        category = CmdTemplateCategory.objects.create(name='validation')
        payload = {'name':'query','category':category.id,'cmd':'systemctl status {{service}}','parameters':[{'name':'service','type':'enum','choices':['sshd','nginx'],'default':'sshd'}]}
        serializer = CmdTemplateSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer = CmdTemplateSerializer(data={**payload, 'parameters':[]})
        self.assertFalse(serializer.is_valid())
