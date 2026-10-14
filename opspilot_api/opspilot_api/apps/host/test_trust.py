import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from paramiko import RSAKey, HostKeys
from rest_framework.test import APIClient
from .models import Host


class HostTrustTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('trust-admin', password='test-password')
        self.user = get_user_model().objects.create_user('trust-user')
        self.host = Host.objects.create(name='test', ip_addr='127.0.0.1', port=52222, username='test')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.key = RSAKey.generate(1024)
        self.public = f'{self.key.get_name()} {self.key.get_base64()}'
        self.fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(self.key.asbytes()).digest()).decode().rstrip('=')
        self.url = f'/host/{self.host.id}/trust'

    def test_preview_does_not_write_and_confirmation_pins_port(self):
        with TemporaryDirectory() as directory, override_settings(SSH_KNOWN_HOSTS=str(Path(directory) / 'known_hosts')):
            path = Path(directory) / 'known_hosts'
            response = self.client.post(self.url, {'public_key': self.public, 'preview': True}, format='json')
            self.assertEqual(response.data['fingerprint'], self.fingerprint)
            self.assertFalse(path.exists())
            response = self.client.post(self.url, {'public_key': self.public, 'fingerprint': self.fingerprint, 'confirmed': True}, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(HostKeys(str(path))['[127.0.0.1]:52222']['ssh-rsa'], self.key)

    def test_regular_user_and_unconfirmed_key_are_rejected(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {'public_key': self.public, 'preview': True}, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(self.url, {'public_key': self.public, 'fingerprint': 'wrong', 'confirmed': True}, format='json').status_code, 400)

    def test_changed_key_is_never_overwritten(self):
        with TemporaryDirectory() as directory, override_settings(SSH_KNOWN_HOSTS=str(Path(directory) / 'known_hosts')):
            self.client.post(self.url, {'public_key': self.public, 'fingerprint': self.fingerprint, 'confirmed': True}, format='json')
            other = RSAKey.generate(1024)
            public = f'{other.get_name()} {other.get_base64()}'
            fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(other.asbytes()).digest()).decode().rstrip('=')
            self.assertEqual(self.client.post(self.url, {'public_key': public, 'fingerprint': fingerprint, 'confirmed': True}, format='json').status_code, 409)
            self.assertEqual(HostKeys(str(Path(directory) / 'known_hosts'))['[127.0.0.1]:52222']['ssh-rsa'], self.key)
