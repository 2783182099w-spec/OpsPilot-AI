from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from host.models import Host
from host.permissions import require_hosts
from rest_framework.exceptions import PermissionDenied

class HostPermissionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="test-only-password")
        self.other = get_user_model().objects.create_user("other", password="test-only-password")
        self.host = Host.objects.create(name="test", ip_addr="127.0.0.1", port=22, username="test")
        self.host.users.add(self.owner)
        self.client = APIClient()
    def test_unassigned_user_cannot_read_or_delete(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/host/{self.host.pk}").status_code, 404)
        self.assertEqual(self.client.delete(f"/host/{self.host.pk}").status_code, 404)
        self.assertTrue(Host.objects.filter(pk=self.host.pk).exists())
    def test_batch_rejects_mixed_authorization(self):
        with self.assertRaises(PermissionDenied):
            require_hosts(self.other, [self.host.pk])
        self.assertEqual(len(require_hosts(self.owner, [self.host.pk])), 1)
    def test_batch_api_rejects_before_ssh(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post("/mtask/cmd_exec", {"host_ids": [self.host.pk], "cmd": "id"}, format="json").status_code, 403)

    def test_search_and_pagination_are_authorized(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get('/host/?page=1&search=test')
        self.assertEqual(response.data['count'], 1)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/host/?page=1').data['count'], 0)

    def test_export_does_not_include_other_assets(self):
        self.client.force_authenticate(self.other)
        self.assertNotIn(b'127.0.0.1', self.client.get('/host/export').content)

    def test_invalid_excel_has_readable_error(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(self.owner)
        response = self.client.post('/host/excel_host', {'host_excel': SimpleUploadedFile('bad.xlsx', b'bad')}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_local_create_never_connects(self):
        from unittest.mock import patch
        from django.test import override_settings
        self.client.force_authenticate(self.owner)
        with override_settings(HOST_SSH_VALIDATE=False), patch('host.serializers.SSHParamiko') as ssh:
            response = self.client.post('/host/', {'name': 'offline', 'ip_addr': '127.0.0.2', 'port': 22, 'username': 'test'}, format='json')
            self.assertEqual(response.status_code, 201)
            ssh.assert_not_called()
            self.assertTrue(Host.objects.get(pk=response.data['id']).users.filter(pk=self.owner.pk).exists())
