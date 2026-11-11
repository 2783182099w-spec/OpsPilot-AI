import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AICall


@override_settings(DEEPSEEK_API_KEY='fixture-default-key', DEEPSEEK_BASE_URL='https://default.example.test',
                   DEEPSEEK_MODEL='existing-default-model')
class ProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = get_user_model().objects.create_user('provider-test')
        self.client.force_authenticate(self.user)

    def upstream(self, request, payload=None, status=200):
        response = MagicMock(status_code=status)
        response.iter_content.return_value = [json.dumps(payload).encode()]
        request.return_value.__enter__.return_value = response

    @patch('ai.services.requests.request')
    def test_default_retains_existing_configuration(self, request):
        self.upstream(request, {'choices': [{'message': {'content': 'analysis'}}], 'usage': {'prompt_tokens': 11}})
        config = self.client.get('/ai/config')
        self.assertEqual(config.data['default'], {'model': 'existing-default-model', 'configured': True})
        self.assertNotContains(config, 'fixture-default-key')
        response = self.client.post('/ai/chat', {'message': 'test'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(request.call_args.args, ('POST', 'https://default.example.test/chat/completions'))
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-default-key')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        self.assertEqual(AICall.objects.get(owner=self.user).input_tokens, 11)

    @patch('ai.services.requests.request')
    def test_personal_models_and_chat_use_only_selected_provider(self, request):
        key = 'fixture-personal-key'
        self.upstream(request, {'data': [{'id': 'gpt-fixture'}, {'id': key}]})
        models = self.client.post('/ai/models', {'provider': 'openai', 'api_key': key}, format='json')
        self.assertEqual(models.data, {'models': ['gpt-fixture']})
        self.assertEqual(request.call_args.args, ('GET', 'https://api.openai.com/v1/models'))
        self.upstream(request, {'choices': [{'message': {'content': 'reply ' + key}}]})
        response = self.client.post('/ai/chat', {
            'message': 'diagnose ' + key, 'configuration': {'provider': 'openai', 'api_key': key, 'model': 'gpt-fixture'},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(request.call_args.args, ('POST', 'https://api.openai.com/v1/chat/completions'))
        self.assertNotIn(key, str(request.call_args.kwargs['json']))
        self.assertEqual(request.call_args.kwargs['json']['max_completion_tokens'], 2000)
        self.assertNotContains(response, key)
        call = AICall.objects.get(owner=self.user)
        self.assertEqual((call.provider, call.model, call.success), ('openai', 'gpt-fixture', True))

    @patch('ai.services.requests.request')
    def test_personal_failure_never_falls_back_or_echoes_provider_error(self, request):
        self.upstream(request, {'error': 'fixture-personal-key'}, status=401)
        response = self.client.post('/ai/chat', {
            'message': 'test', 'configuration': {'provider': 'deepseek', 'api_key': 'fixture-personal-key', 'model': 'custom-model'},
        }, format='json')
        self.assertNotContains(response, 'fixture-personal-key', status_code=502)
        self.assertEqual(request.call_count, 1)
        call = AICall.objects.get(owner=self.user)
        self.assertEqual((call.provider, call.model, call.success), ('deepseek', 'custom-model', False))

    @patch('ai.services.requests.request')
    def test_rejects_untrusted_provider_missing_model_and_invalid_key(self, request):
        for config in [
            {'provider': 'https://127.0.0.1/', 'api_key': 'fixture-personal-key', 'model': 'custom-model'},
            {'provider': 'deepseek', 'api_key': 'fixture-personal-key'},
            {'provider': 'deepseek', 'api_key': 'fixture\r\ninvalid', 'model': 'custom-model'},
        ]:
            response = self.client.post('/ai/chat', {'message': 'test', 'configuration': config}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        request.assert_not_called()
