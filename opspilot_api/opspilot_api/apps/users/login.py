from django.contrib.auth import get_user_model
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer, TokenVerifySerializer
from rest_framework_simplejwt.tokens import UntypedToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

from .tokens import check_session


class AccountTokenSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['sv'] = user.session_version
        return token


class AccountRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = self.token_class(attrs['refresh'])
        user = get_user_model().objects.filter(pk=token.get('user_id')).first()
        check_session(user, token)
        return super().validate(attrs)


class AccountVerifySerializer(TokenVerifySerializer):
    def validate(self, attrs):
        result = super().validate(attrs)
        token = UntypedToken(attrs['token'])
        user = get_user_model().objects.filter(pk=token.get('user_id')).first()
        check_session(user, token)
        return result


class AccountVerifyView(TokenVerifyView):
    serializer_class = AccountVerifySerializer


class LoginThrottle(SimpleRateThrottle):
    rate = '15/minute'
    scope = 'login_ip'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': request.META.get('REMOTE_ADDR') or 'unknown'}


class AccountLoginView(TokenObtainPairView):
    serializer_class = AccountTokenSerializer
    throttle_classes = [LoginThrottle]


class AccountRefreshView(TokenRefreshView):
    serializer_class = AccountRefreshSerializer
