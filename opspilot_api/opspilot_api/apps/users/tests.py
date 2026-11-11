from base64 import b64encode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from host.models import Host
from .models import AccountAudit
from .tokens import terminal_user

User = get_user_model()
PASSWORD = 'Fixture-Only:Long-pass42'


class AccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('account-admin', password=PASSWORD)
        self.operator = User.objects.create_user('account-operator', password=PASSWORD)
        self.host = Host.objects.create(name='Allowed host', ip_addr='192.0.2.10', port=22, username='ops')
        self.other_host = Host.objects.create(name='Other host', ip_addr='192.0.2.11', port=22, username='ops')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def login(self, username):
        client = APIClient()
        response = client.post('/users/login/', {'username': username, 'password': PASSWORD}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_create_account_with_scoped_hosts_and_audit(self):
        response = self.client.post('/users/accounts/', {
            'username': 'limited-observer', 'password': PASSWORD,
            'role': 'observer', 'host_ids': [self.host.pk],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('password', response.data)
        user = User.objects.get(username='limited-observer')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.is_staff)
        self.assertEqual(response.data['host_ids'], [self.host.pk])
        event = AccountAudit.objects.get(target=user)
        self.assertEqual(event.actor, self.admin)
        self.assertNotIn(PASSWORD, str(event.changes))
        rows = self.client.get('/users/accounts/', {'role': 'observer', 'search': 'limited', 'active': 'true'})
        self.assertEqual(rows.data['count'], 1)

    def test_basic_auth_cannot_bypass_login(self):
        basic = b64encode(f'{self.operator.username}:{PASSWORD}'.encode()).decode()
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Basic ' + basic)
        self.assertEqual(self.client.get('/users/me/').status_code, 401)

    def test_non_admin_cannot_manage_accounts_or_audit(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get('/users/accounts/').status_code, 403)
        self.assertEqual(self.client.get('/users/audit/').status_code, 403)
        self.assertEqual(self.client.patch(f'/users/accounts/{self.admin.pk}/', {'role': 'operator'}).status_code, 403)
        self.assertEqual(self.client.post(f'/users/accounts/{self.admin.pk}/password/', {'password': PASSWORD}).status_code, 403)

    def test_cannot_remove_current_admin(self):
        url = f'/users/accounts/{self.admin.pk}/'
        for data in ({'is_active': False}, {'role': 'operator'}):
            self.assertEqual(self.client.patch(url, data).status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser and self.admin.is_active)

    def test_scope_change_revokes_access_refresh_and_terminal(self):
        self.host.users.add(self.operator)
        tokens = self.login(self.operator.username)
        old = APIClient()
        old.credentials(HTTP_AUTHORIZATION='jwt ' + tokens['access'])
        self.assertEqual(old.get('/users/me/').status_code, 200)
        self.assertIsNotNone(terminal_user(tokens['access']))
        result = self.client.patch(f'/users/accounts/{self.operator.pk}/', {'host_ids': []}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(old.get('/users/me/').status_code, 401)
        self.assertEqual(old.post('/users/api/', {'token': tokens['access']}).status_code, 401)
        with self.assertRaises(AuthenticationFailed):
            terminal_user(tokens['access'])
        self.assertEqual(old.post('/users/refresh/', {'refresh': tokens['refresh']}).status_code, 401)
        fresh = self.login(self.operator.username)
        old.credentials(HTTP_AUTHORIZATION='jwt ' + fresh['access'])
        self.assertEqual(old.get('/host/').data, [])

    def test_observer_cannot_execute_or_connect_sftp(self):
        self.operator.platform_role = 'observer'
        self.operator.save(update_fields=['platform_role'])
        self.host.users.add(self.operator)
        self.client.force_authenticate(self.operator)
        self.assertEqual([row['id'] for row in self.client.get('/host/').data], [self.host.pk])
        self.assertEqual(self.client.get(f'/host/{self.other_host.pk}').status_code, 404)
        for path in ('/host/', '/host/category', '/mtask/tasks', '/mtask/cmd_exec', '/ai/chat', '/ai/models'):
            self.assertEqual(self.client.post(path, {}, format='json').status_code, 403, path)
        self.assertEqual(self.client.get(f'/host/{self.host.pk}/files').status_code, 403)
        self.assertEqual(self.client.get(f'/host/{self.host.pk}/files?history=1').status_code, 200)

    def test_self_password_requires_current_password_and_revokes_session(self):
        tokens = self.login(self.operator.username)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='jwt ' + tokens['access'])
        url = '/users/me/password/'
        new_password = 'Another-Fixture:Long-pass66'
        self.assertEqual(client.post(url, {'password': new_password}).status_code, 400)
        result = client.post(url, {'password': new_password, 'current_password': PASSWORD})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(client.get('/users/me/').status_code, 401)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.check_password(new_password))
        self.assertEqual(AccountAudit.objects.get(target=self.operator).changes, {})
