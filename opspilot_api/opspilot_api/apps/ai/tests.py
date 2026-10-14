from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from opspilot_api.utils.redaction import redact


class AITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user('ai-test'))

    @patch('ai.views.ask_deepseek', return_value='analysis')
    def test_redacts_secrets_before_call(self, call):
        response = self.client.post('/ai/chat', {'message': 'password=example-secret', 'mode': 'logs'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('example-secret', call.call_args[0][0])

    @patch('ai.views.ask_deepseek')
    def test_rejects_oversized_input(self, call):
        self.assertEqual(self.client.post('/ai/chat', {'message': 'x' * 20001}, format='json').status_code, 400)
        call.assert_not_called()

    def test_private_key_redacted(self):
        self.assertNotIn('secret', redact('-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----'))
