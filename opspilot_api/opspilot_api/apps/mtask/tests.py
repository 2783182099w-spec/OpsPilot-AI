from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from host.models import Host
from mtask.models import ExecutionTask
from mtask.tasks import run_task


class TaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator')
        self.admin = get_user_model().objects.create_superuser('admin', 'admin@example.test', 'test-password')
        self.host = Host.objects.create(name='test', ip_addr='127.0.0.1', port=22, username='test')
        self.host.users.add(self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def submit(self):
        return self.client.post('/mtask/tasks', {'host_ids': [self.host.id], 'cmd': 'id', 'confirmed': True}, format='json')

    def test_operator_requires_approval(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'awaiting_approval')
        self.assertEqual(self.client.post(f"/mtask/tasks/{response.data['id']}").status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(f"/mtask/tasks/{response.data['id']}").data['status'], 'pending')

    def test_history_is_private(self):
        task_id = self.submit().data['id']
        self.client.force_authenticate(get_user_model().objects.create_user('other'))
        self.assertEqual(self.client.get(f'/mtask/tasks/{task_id}').status_code, 404)

    @patch('mtask.tasks.SSHParamiko')
    def test_revoked_authorization_does_not_connect(self, ssh):
        task = ExecutionTask.objects.create(owner=self.user, command='id', host_ids=[self.host.id])
        self.host.users.clear()
        run_task(task)
        ssh.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
