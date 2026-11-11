from django.conf import settings
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from opspilot_api.utils.redaction import redact
from users.access import OpsAccess
from .models import AICall
from .services import PROVIDERS, DeepSeekError, ask_deepseek, available_models, default_model


class AIThrottle(UserRateThrottle):
    rate = '10/minute'
    scope = 'ai_chat'


class ModelThrottle(UserRateThrottle):
    rate = '5/minute'
    scope = 'ai_models'


class ProviderInput(serializers.Serializer):
    provider = serializers.ChoiceField(choices=list(PROVIDERS))
    api_key = serializers.RegexField(regex=r'^[!-~]{8,512}\Z', write_only=True)


class PersonalConfig(ProviderInput):
    model = serializers.RegexField(regex=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}\Z')

    def validate(self, attrs):
        if attrs['api_key'] in attrs['model']:
            raise serializers.ValidationError({'model': '模型 ID 不能包含 API Key'})
        return attrs


class ChatInput(serializers.Serializer):
    message = serializers.CharField(max_length=20000)
    history = serializers.ListField(child=serializers.DictField(), max_length=20, required=False)
    mode = serializers.ChoiceField(choices=('chat', 'logs', 'diagnosis'), default='chat')
    configuration = PersonalConfig(required=False, allow_null=True)

    def validate_history(self, history):
        clean = []
        for item in history:
            role, content = item.get('role'), item.get('content')
            if role in ('user', 'assistant') and isinstance(content, str) and content:
                clean.append({'role': role, 'content': content})
        if sum(len(item['content']) for item in clean) > 40000:
            raise serializers.ValidationError('上下文过长')
        return clean


class ChatView(APIView):
    permission_classes = [OpsAccess]
    throttle_classes = [AIThrottle]

    def post(self, request):
        serializer = ChatInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = data.get('configuration') or {}
        history = [{**item, 'content': redact(item['content'])} for item in data.get('history', [])]
        try:
            reply = ask_deepseek(redact(data['message']), history=history, mode=data['mode'],
                                 owner=request.user, **config)
        except DeepSeekError as exc:
            AICall.objects.create(owner=request.user, model=config.get('model', default_model()),
                                  provider=config.get('provider', 'default'), mode=data['mode'])
            return Response({'error': str(exc)}, status=502)
        return Response({'reply': reply})


class AIConfigView(APIView):
    def get(self, request):
        return Response({'default': {'model': default_model(), 'configured': bool(getattr(settings, 'DEEPSEEK_API_KEY', ''))},
                         'providers': [{'id': key, 'label': name} for key, (name, _) in PROVIDERS.items()]})


class AIModelsView(APIView):
    throttle_classes = [ModelThrottle]

    def post(self, request):
        serializer = ProviderInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response({'models': available_models(**serializer.validated_data)})
        except DeepSeekError as exc:
            return Response({'error': str(exc)}, status=502)


class AIHistoryView(APIView):
    def get(self, request):
        calls = AICall.objects.filter(owner=request.user).order_by('-id')
        return Response(list(calls.values('id', 'provider', 'model', 'mode', 'input_tokens',
                                          'output_tokens', 'success', 'created_at')[:50]))
