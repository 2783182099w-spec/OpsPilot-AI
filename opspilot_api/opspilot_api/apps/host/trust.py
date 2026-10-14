"""仅登记由管理员从可信渠道获得的公钥，不进行网络扫描。"""
import base64
import hashlib
import os
import tempfile
from pathlib import Path
from threading import Lock

from django.conf import settings
from django.shortcuts import get_object_or_404
from paramiko import HostKeys
from paramiko.hostkeys import HostKeyEntry
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .permissions import accessible_hosts

_lock = Lock()


class HostTrustView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.is_superuser:
            return Response({"error": "只有管理员可以登记服务器指纹"}, status=403)
        host = get_object_or_404(accessible_hosts(request.user), pk=pk)
        public_key = request.data.get('public_key')
        if not isinstance(public_key, str) or len(public_key) > 8192 or '\n' in public_key.strip() or '\r' in public_key.strip():
            return Response({"error": "请粘贴一行完整的 SSH 主机公钥"}, status=400)
        address = host.ip_addr or ''
        if not address or any(char.isspace() for char in address) or ',' in address:
            return Response({"error": "主机地址格式无效"}, status=400)
        target = address if host.port == 22 else f'[{address}]:{host.port}'
        try:
            entry = HostKeyEntry.from_line(f'{target} {public_key.strip()}')
            if entry is None or entry.key is None:
                raise ValueError()
            key = entry.key
            fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        except Exception:
            return Response({"error": "无法解析公钥，请使用 ssh-ed25519、ecdsa-sha2-* 或 ssh-rsa 开头的主机公钥"}, status=400)
        if request.data.get('preview') is True:
            return Response({"target": target, "fingerprint": fingerprint, "key_type": key.get_name()})
        if request.data.get('confirmed') is not True or request.data.get('fingerprint') != fingerprint:
            return Response({"error": "请核对可信来源的 SHA256 指纹后确认登记"}, status=400)
        configured_path = getattr(settings, 'SSH_KNOWN_HOSTS', '')
        if not configured_path:
            return Response({"error": "未配置 SSH_KNOWN_HOSTS"}, status=400)
        path = Path(configured_path)
        try:
            with _lock:
                keys = HostKeys()
                if path.exists():
                    keys.load(str(path))
                existing = keys.lookup(target)
                if existing and key.get_name() in existing and existing[key.get_name()] != key:
                    return Response({"error": "已登记公钥与新公钥不一致，拒绝覆盖；请先核查是否重装系统或存在安全风险"}, status=409)
                keys.add(target, key.get_name(), key)
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(dir=path.parent)
                os.close(fd)
                try:
                    keys.save(temp_path)
                    os.replace(temp_path, path)
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
        except (OSError, ValueError):
            return Response({"error": "无法写入指纹文件，请检查文件权限和配置"}, status=500)
        return Response({"message": "主机指纹已登记，可以重新同步登录公钥", "fingerprint": fingerprint})
