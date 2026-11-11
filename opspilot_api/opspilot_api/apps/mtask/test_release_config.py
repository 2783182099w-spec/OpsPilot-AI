from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from host.models import Host
from .models import ExecutionTask, ReleaseApplication, ReleaseArtifact
from .release_config import verify_precheck

User = get_user_model()


class ReleaseConfigTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('ci-admin')
        self.operator = User.objects.create_user('ci-operator')
        self.host = Host.objects.create(name='linux',ip_addr='192.0.2.14',port=22,username='fixture',environment='test')
        self.target = {'application':'demo','unit':'demo.service','host_ids':[self.host.pk],
                       'environment':'test','origin':'https://artifacts.example.test','port':8080,'path':'/health'}
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.ci = APIClient()
        self.profile_url = '/mtask/release-applications'
        self.url = '/mtask/ci/demo/artifacts'
        self.artifact = {'version':'1.0.0','url':'https://artifacts.example.test/demo','sha256':'a'*64}

    def register(self):
        result = self.client.post(self.profile_url,self.target,format='json')
        self.assertEqual(result.status_code,201,result.data)
        return result.data

    def token(self, action='rotate'):
        return self.client.post(self.profile_url+'/demo/token',{'action':action},format='json')

    def test_configuration_is_live_and_versioned(self):
        initial = self.register()
        listing = self.client.get('/mtask/releases').data['applications']
        self.assertEqual(listing[0]['host_ids'],[self.host.pk])
        self.assertTrue(listing[0]['managed'])
        self.assertEqual(self.client.post(self.profile_url,self.target,format='json').status_code,400)
        disabled = self.client.post(self.profile_url,{**self.target,'enabled':False,'version':initial['version']},format='json')
        self.assertEqual(disabled.status_code,200)
        self.assertEqual(self.client.get('/mtask/releases').data['applications'],[])
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(self.profile_url).status_code,403)
        self.assertEqual(self.client.post(self.profile_url,self.target,format='json').status_code,403)

    def test_ci_token_is_scoped_rotatable_and_cannot_dispatch(self):
        self.register()
        token_response = self.token()
        old = token_response.data['token']
        self.assertIn('no-store',token_response['Cache-Control'])
        self.assertNotContains(self.client.get(self.profile_url),old)
        self.assertNotEqual(ReleaseApplication.objects.get().ci_digest,old)
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json').status_code,403)
        self.assertEqual(self.ci.post('/mtask/ci/another/artifacts',self.artifact,format='json',
                                     HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,403)
        invalid = {**self.artifact,'url':'https://other.example.test/demo'}
        self.assertEqual(self.ci.post(self.url,invalid,format='json',HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,400)
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,201)
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,200)
        invalid = {**self.artifact,'sha256':'b'*64}
        self.assertEqual(self.ci.post(self.url,invalid,format='json',HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,400)
        self.assertEqual(ReleaseArtifact.objects.count(),1)
        self.assertFalse(ExecutionTask.objects.exists())
        new = self.token().data['token']
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=old).status_code,403)
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=new).status_code,200)
        self.token('revoke')
        self.assertEqual(self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=new).status_code,403)

    def test_ci_artifact_requires_regular_release_approval(self):
        self.register()
        token = self.token().data['token']
        artifact = self.ci.post(self.url,self.artifact,format='json',HTTP_X_OPSPILOT_CI_TOKEN=token).data
        body = {'application':'demo','artifact_id':artifact['id'],'host_ids':[self.host.pk],'reason':'test fixture'}
        preview = self.client.post('/mtask/releases',body,format='json')
        self.assertEqual(preview.status_code,200,preview.data)
        submit = self.client.post('/mtask/releases',{**body,'token':preview.data['token'],'confirmed':True},format='json')
        self.assertEqual(submit.status_code,202,submit.data)
        self.assertEqual(ExecutionTask.objects.get(pk=submit.data['task_id']).status,'awaiting_approval')

    def test_precheck_is_queued_and_rechecks_profile(self):
        data = self.register()
        url = self.profile_url+'/demo/precheck'
        result = self.client.post(url,{'host_ids':[self.host.pk],'confirmed':True},format='json')
        self.assertEqual(result.status_code,202,result.data)
        task = ExecutionTask.objects.get(pk=result.data['id'])
        verify_precheck(task,[self.host])
        self.client.post(self.profile_url,{**self.target,'version':data['version'],'path':'/changed'},format='json')
        with self.assertRaises(ValidationError):
            verify_precheck(task,[self.host])

    @override_settings(OPSPILOT_RELEASE_TARGETS={'legacy':{'unit':'demo.service','host_ids':[1],
        'environment':'test','origin':'https://artifacts.example.test','port':8080,'path':'/health'}})
    def test_disabled_profile_does_not_fall_back(self):
        row = self.client.post(self.profile_url,{**self.target,'application':'legacy','enabled':False},format='json')
        self.assertEqual(row.status_code,201)
        self.assertEqual(self.client.get('/mtask/releases').data['applications'],[])
