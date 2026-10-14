from unittest.mock import patch
from django.test import TestCase, override_settings
from cryptography.fernet import Fernet
from paramiko import RejectPolicy
from host.models import PkeyModel
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko


class SSHSafetyTests(TestCase):
    def test_unknown_host_key_is_not_accepted(self):
        with patch('opspilot_api.utils.ssh.SSHClient') as client:
            SSHParamiko('example.test', password='test').get_connected_client()
            policy = client.return_value.set_missing_host_key_policy.call_args[0][0]
            self.assertIsInstance(policy, RejectPolicy)
            self.assertFalse(client.return_value.connect.call_args.kwargs['allow_agent'])

    def test_key_is_encrypted_at_rest(self):
        with override_settings(SSH_KEY_ENCRYPTION_KEY=Fernet.generate_key().decode(), SSH_REQUIRE_ENCRYPTED_KEYS=True):
            PkeyManager.set('unit-test', 'test-only-key', 'test-public-key')
            self.assertTrue(PkeyModel.objects.get(name='unit-test').private.startswith('fernet:'))
            self.assertEqual(PkeyManager.get('unit-test')[0], 'test-only-key')
        PkeyManager.get.cache_clear()
