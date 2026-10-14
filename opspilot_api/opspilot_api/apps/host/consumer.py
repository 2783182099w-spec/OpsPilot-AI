"""WebSocket 终端消费者的实现。

浏览器通过 ``ws/ssh/<host_id>/?token=<JWT>`` 连接，本消费者负责：

1. 校验 JWT，未登录直接拒绝；
2. 使用全局 SSH 密钥对连接目标主机；
3. 把 SSH shell 的输出实时转发给浏览器，把浏览器的输入写回 SSH。
"""

import logging
from threading import Thread
from urllib.parse import parse_qs

from channels.generic.websocket import WebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model

from host.models import Host
from host.models import SSHSession
from django.utils import timezone
from host.permissions import accessible_hosts
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko

logger = logging.getLogger("django")


class SSHCmdConsumer(WebsocketConsumer):
    """基于 paramiko 的 Web SSH 终端。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host_id = None
        self.ssh = None
        self.ssh_chan = None
        self.reader = None
        self.audit_id = None

    # ------------------------------------------------------------------ 鉴权
    def _token(self):
        raw = self.scope.get("query_string", b"")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        return (parse_qs(raw).get("token") or [""])[0]

    @staticmethod
    def _get_user(token):
        if not token:
            return None
        try:
            from rest_framework_simplejwt.tokens import AccessToken

            validated = AccessToken(token)
            return get_user_model().objects.filter(pk=validated["user_id"], is_active=True).first()
        except Exception as exc:  # noqa: BLE001 - 任何校验异常都视为未登录
            logger.info("WebSocket token 校验失败: %s", exc)
            return None

    # ------------------------------------------------------------ 连接生命周期
    def connect(self):
        self.host_id = self.scope["url_route"]["kwargs"]["id"]
        user = self._get_user(self._token())
        if user is None:
            self.close(code=4401)
            return
        if not accessible_hosts(user).filter(pk=self.host_id).exists():
            self.close(code=4403)
            return
        self.accept()
        self.audit_id = SSHSession.objects.create(owner=user, host_id=self.host_id).id

        if self._get_user(self._token()) is None:
            self._reject("认证失败：登录状态无效，请重新登录后再打开终端。")
            return

        host = Host.objects.filter(pk=self.host_id).first()
        if host is None:
            self._reject(f"主机不存在（id={self.host_id}）。")
            return

        self.send(f"正在连接 {host.username}@{host.ip_addr}:{host.port} ...\r\n")

        try:
            private_key, _ = PkeyManager.get(settings.DEFAULT_KEY_NAME)
        except KeyError:
            self._reject("尚未生成全局 SSH 密钥对，请先在「资产管理」中成功添加一台主机。")
            return

        try:
            self.ssh = SSHParamiko(host.ip_addr, host.port, host.username, private_key)
            if self.ssh.get_connected_client() is None:
                raise ConnectionError(self.ssh.error_message or "无法建立 SSH 连接")
            self.ssh_chan = self.ssh.client.invoke_shell(term="xterm")
            self.ssh_chan.transport.set_keepalive(30)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SSH 连接失败 host=%s: %s", host.ip_addr, exc)
            self._reject(
                f"连接失败：{exc}\r\n"
                "请检查主机地址/端口/账号是否正确，以及该主机的 ~/.ssh/authorized_keys "
                "是否已包含系统的全局公钥。"
            )
            return

        self.reader = Thread(target=self._read_loop, daemon=True)
        SSHSession.objects.filter(pk=self.audit_id).update(status="connected")
        self.reader.start()

    def _reject(self, reason):
        if self.audit_id:
            SSHSession.objects.filter(pk=self.audit_id).update(status="failed", ended_at=timezone.now())
        try:
            self.send(f"{reason}\r\n")
        except Exception:  # noqa: BLE001 - 连接可能已经断开
            pass
        self.close()

    def _read_loop(self):
        """持续读取 SSH 输出并推送到浏览器。"""
        try:
            while True:
                data = self.ssh_chan.recv(64 * 1024)
                if not data:
                    break
                self.send(data.decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001 - 通道关闭属于正常情况
            pass
        finally:
            try:
                self.close()
            except Exception:  # noqa: BLE001
                pass

    def receive(self, text_data=None, bytes_data=None):
        if self.ssh_chan is None:
            return
        payload = text_data if text_data is not None else (bytes_data or b"").decode("utf-8", "ignore")
        try:
            self.ssh_chan.send(payload)
        except Exception:  # noqa: BLE001
            self.close()

    def disconnect(self, code):
        if self.audit_id:
            SSHSession.objects.filter(pk=self.audit_id, ended_at__isnull=True).update(status="closed", ended_at=timezone.now())
        if self.ssh_chan is not None:
            try:
                self.ssh_chan.close()
            except Exception:  # noqa: BLE001
                pass
        if self.ssh is not None and self.ssh.client is not None:
            try:
                self.ssh.client.close()
            except Exception:  # noqa: BLE001
                pass
