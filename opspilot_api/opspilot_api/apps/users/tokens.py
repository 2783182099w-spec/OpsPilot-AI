from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken


def check_session(user, token):
    if user is None or not user.is_active or token.get('sv', 0) != user.session_version:
        raise AuthenticationFailed('登录状态已撤销，请重新登录', code='session_revoked')
    return user


class AccountJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        return check_session(super().get_user(validated_token), validated_token)


def terminal_user(raw):
    return AccountJWTAuthentication().get_user(AccessToken(raw))
