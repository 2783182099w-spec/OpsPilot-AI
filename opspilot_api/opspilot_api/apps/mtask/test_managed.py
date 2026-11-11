from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from host.models import Host
from .engine import acquire_lease
from .models import ExecutionTask, ManagedJob
from .rollout import execute_host
from .tasks import run_task


class ManagedJobsTests(TestCase):
    def setUp(self):
        users=get_user_model()
        self.admin=users.objects.create_superuser('author',password='offline-test')
        self.reviewer=users.objects.create_superuser('reviewer',password='offline-test')
        self.operator=users.objects.create_user('operator')
        self.hosts=[Host.objects.create(name=f'fake-{i}',ip_addr=f'127.0.0.{i+10}',port=22222,username='ops') for i in range(5)]
        for host in self.hosts[:4]:
            host.users.add(self.operator)
        self.ids=[h.id for h in self.hosts[:4]]
        self.client=APIClient()
        self.client.force_authenticate(self.admin)
        self.definition={'name':'读状态','kind':'shell','risk':'read_only','description':'用于隔离测试',
            'source':'printf "%s\\n" "$1"','parameters':[{'name':'value','type':'integer','min':1,'max':10,'default':2}],
            'environments':['test'],'concurrency':1,'canary_count':1,'batch_size':2,'failure_limit':1}
        self.body={'host_ids':self.ids,'parameters':{'value':3},'reason':'用例'}

    def publish(self, definition=None):
        response=self.client.post('/mtask/jobs',{'definition':definition or self.definition},format='json')
        self.assertEqual(response.status_code,201,response.data)
        job=response.data
        result=self.client.post(f"/mtask/jobs/{job['id']}",{'action':'publish','version':job['version']},format='json')
        self.assertEqual(result.status_code,200,result.data)
        return result.data

    def queue(self, job, body=None):
        body=body or self.body
        url=f"/mtask/jobs/{job['id']}"
        plan=self.client.post(url+'/preview',body,format='json')
        self.assertEqual(plan.status_code,200,plan.data)
        response=self.client.post(url+'/run',{**body,'token':plan.data['token'],'confirmed':True},format='json')
        self.assertEqual(response.status_code,202,response.data)
        return ExecutionTask.objects.get(pk=response.data['id'])

    def action(self,task,action,**extra):
        return self.client.post(f'/mtask/tasks/{task.id}/action',{'action':action,'confirmed':True,**extra},format='json')

    def run_worker(self,task, statuses):
        calls=[]
        def stub(host,payload,service,key,stop,events,initial):
            calls.append(host.id)
            status=statuses.get(host.id,'success')
            return {'status':status,'exit_code':0 if status=='success' else 1,'output':'fixture',
                'stages':initial,'may_have_run':True,'failure_code':'' if status=='success' else 'script_exit'}
        task.refresh_from_db()
        self.assertEqual(task.status,'pending')
        ExecutionTask.objects.filter(pk=task.id).update(status='running',started_at=timezone.now())
        task.refresh_from_db()
        with patch('mtask.rollout.PkeyManager.get',return_value=('fake','fake')),patch('mtask.rollout.execute_host',side_effect=stub):
            run_task(task)
        task.refresh_from_db()
        return calls

    def test_library_permissions_and_revision_snapshot(self):
        job=self.publish()
        task=self.queue(job)
        response=self.client.post(f"/mtask/jobs/{job['id']}",{'action':'save','version':job['version'],'definition':{**self.definition,'source':'echo changed'}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['state'],'draft')
        self.assertEqual(ManagedJob.objects.get(pk=job['id']).revisions.count(),3)
        task.refresh_from_db()
        self.assertEqual(task.command,self.definition['source'])
        self.assertEqual(task.options['managed']['version'],job['version'])
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get('/mtask/jobs').data,[])
        self.assertEqual(self.client.post('/mtask/jobs',{'definition':self.definition},format='json').status_code,403)

    def test_preview_binds_user_version_order_and_host_connection(self):
        job=self.publish()
        url=f"/mtask/jobs/{job['id']}"
        plan=self.client.post(url+'/preview',self.body,format='json')
        token=plan.data['token']
        for change in ({'host_ids':self.ids[::-1]},{'parameters':{'value':9}},{'parameters':{'value':11}}):
            self.assertEqual(self.client.post(url+'/run',{**self.body,**change,'token':token,'confirmed':True},format='json').status_code,400)
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.post(url+'/run',{**self.body,'token':token,'confirmed':True},format='json').status_code,400)
        self.assertEqual(self.client.post(url+'/preview',{**self.body,'host_ids':[self.hosts[4].id]},format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        Host.objects.filter(pk=self.ids[0]).update(port=22)
        self.assertEqual(self.client.post(url+'/run',{**self.body,'token':token,'confirmed':True},format='json').status_code,400)
        self.assertFalse(ExecutionTask.objects.exists())

    def test_first_host_failure_prevents_any_other_dispatch(self):
        task=self.queue(self.publish())
        self.assertEqual(self.run_worker(task,{self.ids[0]:'failed'}),self.ids[:1])
        self.assertEqual(task.status,'failed')
        self.assertEqual(task.host_results.filter(status='skipped',may_have_run=False).count(),3)
        response=self.client.get(f'/mtask/tasks/{task.id}?failure_code=not_dispatched')
        self.assertEqual(response.data['results_count'],3)
        self.assertEqual(self.action(task,'release').status_code,400)

    def test_success_pauses_then_release_and_failure_limit_stops_remaining(self):
        task=self.queue(self.publish(),{**self.body,'host_ids':self.ids[::-1]})
        self.assertEqual(self.run_worker(task,{}),self.ids[-1:])
        self.assertEqual(task.status,'awaiting_release')
        self.assertEqual(task.completed_hosts,1)
        self.assertEqual(task.host_results.filter(status='queued').count(),3)
        self.assertEqual(self.action(task,'release').status_code,200)
        self.assertEqual(self.action(task,'release').status_code,400)
        self.assertEqual(self.run_worker(task,{self.ids[2]:'failed'}),[self.ids[2]])
        self.assertEqual(task.status,'failed')
        self.assertEqual(task.host_results.get(host_id_snapshot=self.ids[-1]).batch,1)
        self.assertEqual(task.host_results.get(host_id_snapshot=self.ids[2]).batch,2)

    def test_rejected_managed_job_keeps_explicit_not_dispatched_evidence(self):
        job=self.publish()
        self.client.force_authenticate(self.operator)
        task=self.queue(job)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.action(task,'reject',reason='范围未核对').status_code,200)
        task.refresh_from_db()
        self.assertEqual(task.status,'rejected')
        self.assertEqual(task.stop_reason,'范围未核对')
        self.assertEqual(task.host_results.filter(status='skipped',may_have_run=False).count(),4)

    def test_protected_job_requires_different_reviewer_and_release(self):
        job=self.publish({**self.definition,'risk':'change','verify_service':'nginx.service'})
        task=self.queue(job)
        self.assertEqual(task.status,'awaiting_approval')
        self.assertEqual(self.action(task,'approve').status_code,403)
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.action(task,'approve').status_code,200)
        self.run_worker(task,{})
        self.assertEqual(task.status,'awaiting_release')
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.action(task,'release').status_code,403)
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.action(task,'release').status_code,200)

    def test_unapproved_target_drift_stops_even_the_first_host(self):
        task=self.queue(self.publish())
        Host.objects.filter(pk=self.ids[-1]).update(environment='production')
        self.assertEqual(self.run_worker(task,{}),[])
        self.assertEqual(task.status,'failed')
        self.assertEqual(task.host_results.filter(status='skipped',may_have_run=False).count(),4)

    def test_host_snapshot_changes_block_release(self):
        task=self.queue(self.publish())
        self.run_worker(task,{})
        Host.objects.filter(pk=self.ids[1]).update(environment='production')
        self.assertEqual(self.action(task,'release').status_code,400)
        self.assertEqual(self.action(task,'cancel').status_code,200)
        self.assertEqual(task.host_results.filter(status='skipped',may_have_run=False).count(),3)

    def test_retry_requires_review_when_script_may_have_run(self):
        task=self.queue(self.publish())
        self.run_worker(task,{self.ids[0]:'failed'})
        self.assertEqual(self.action(task,'retry',host_ids=self.ids[:1]).status_code,400)
        result=self.action(task,'retry',host_ids=self.ids[:1],acknowledge_remote=True)
        self.assertEqual(result.status_code,202,result.data)
        retry=ExecutionTask.objects.get(pk=result.data['id'])
        self.assertEqual(retry.command,task.command)
        self.assertEqual(retry.host_ids,self.ids[:1])
        self.assertEqual(retry.options['managed']['retry_of'],task.id)

    def test_lease_recovery_preserves_unknown_vs_never_dispatched(self):
        task=self.queue(self.publish())
        ExecutionTask.objects.filter(pk=task.id).update(status='running',worker_token='old')
        task.host_results.filter(host_id_snapshot=self.ids[0]).update(status='running')
        self.assertTrue(acquire_lease())
        task.refresh_from_db()
        self.assertEqual(task.status,'interrupted')
        self.assertIsNone(task.host_results.get(host_id_snapshot=self.ids[0]).may_have_run)
        self.assertEqual(task.host_results.filter(status='skipped',may_have_run=False).count(),3)

    def test_verify_failure_is_distinct_from_script_exit(self):
        from queue import SimpleQueue
        from threading import Event
        def script(ssh,payload,stop,output,stage):
            stage('upload','running')
            stage('upload','success')
            stage('preflight','running')
            stage('preflight','success')
            stage('execute','running')
            stage('execute','success')
            return 0,'script ok'
        with patch('mtask.rollout.SSHParamiko') as ssh,patch('mtask.rollout.execute_script',side_effect=script):
            ssh.return_value.execute_cmd.return_value=(3,'inactive')
            result=execute_host(self.hosts[0],{},'nginx.service','fake',Event(),SimpleQueue(),[])
        self.assertEqual(result['failure_code'],'verification_failed')
        self.assertEqual(result['exit_code'],0)
        self.assertTrue(result['may_have_run'])
        self.assertEqual(result['stages'][-1]['name'],'verify')
        self.assertEqual(result['stages'][-1]['status'],'failed')
