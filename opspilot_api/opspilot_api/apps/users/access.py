from rest_framework.permissions import IsAuthenticated, SAFE_METHODS


def role_of(user):
    return 'admin' if user.is_superuser else user.platform_role


def is_observer(user):
    return not user.is_superuser and user.platform_role=='observer'


class OpsAccess(IsAuthenticated):
    message='只读观察员不能提交操作'

    def has_permission(self,request,view):
        return super().has_permission(request,view) and (
            request.method in SAFE_METHODS or not is_observer(request.user))
