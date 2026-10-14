from rest_framework.exceptions import PermissionDenied

from .models import Host


def accessible_hosts(user):
    queryset = Host.objects.filter(is_deleted=False)
    if not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset
    return queryset.filter(users=user).distinct()


def require_hosts(user, ids):
    if not isinstance(ids, list) or not ids or len(ids) > 50:
        raise PermissionDenied("请选择 1 到 50 台已授权主机")
    if any(type(value) is not int for value in ids):
        raise PermissionDenied("主机 ID 必须是整数")
    hosts = list(accessible_hosts(user).filter(id__in=ids))
    if {host.id for host in hosts} != set(ids):
        raise PermissionDenied("主机不存在或没有操作权限")
    return hosts
