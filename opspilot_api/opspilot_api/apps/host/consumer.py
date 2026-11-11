"""WebSocket 终端消费者的实现。

浏览器通过 ``ws/ssh/<host_id>/?token=<JWT>`` 连接，本消费者负责：

1. 校验 JWT，未登录直接拒绝；
2. 使用全局 SSH 密钥对连接目标主机；
3. 把 SSH shell 的输出实时转发给浏览器，把浏览器的输入写回 SSH。
"""

import logging
import json
import socket
import time
import codecs
from threading import Thread, Event, BoundedSemaphore, Lock
from urllib.parse import parse_qs

from channels.generic.websocket import WebsocketConsumer
from django.conf import settings
from django.db import close_old_connections
from users.access import is_observer
from users.tokens import terminal_user

from host.models import Host
from host.models import SSHSession
from host.session_records import TextRecorder, prune_text
from django.utils import timezone
from host.permissions import accessible_hosts
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko

logger = logging.getLogger("django")
_terminal_slots = BoundedSemaphore(12)


class SSHCmdConsumer(WebsocketConsumer):
    """基于 paramiko 的 Web SSH 终端。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host_id = None
        self.ssh = None
        self.ssh_chan = None
        self.reader = None
        self.audit_id = None
        self.recorder = None
        self.stop_event = Event()
        self.slot_acquired = False
        self.last_input = time.monotonic()
        self.token_expires = 0
        self.last_auth_check = 0
        self.authorized = False
        self.cleanup_lock = Lock()

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
            return terminal_user(token)
        except Exception:
            return None

    def _still_authorized(self):
        now = time.monotonic()
        if now-self.last_auth_check >= 5:
            close_old_connections()
            user = self._get_user(self._token())
            self.authorized = bool(user and not is_observer(user) and
                                   accessible_hosts(user).filter(pk=self.host_id).exists())
            self.last_auth_check = now
        return self.authorized

    # ------------------------------------------------------------ 连接生命周期
    def connect(self):
        self.host_id = self.scope["url_route"]["kwargs"]["id"]
        user = self._get_user(self._token())
        if user is None:
            self.close(code=4401)
            return
        if is_observer(user) or not accessible_hosts(user).filter(pk=self.host_id).exists():
            self.close(code=4403)
            return
        if not _terminal_slots.acquire(blocking=False):
            self.close(code=4429)
            return
        self.slot_acquired = True
        from rest_framework_simplejwt.tokens import AccessToken
        self.token_expires = int(AccessToken(self._token())['exp'])
        self.accept()
        self.audit_id = SSHSession.objects.create(owner=user, host_id=self.host_id).id

        if not self._still_authorized():
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
            self.ssh_chan.settimeout(1)
            self.ssh_chan.transport.set_keepalive(30)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SSH 连接失败 host=%s: %s", host.ip_addr, exc)
            self._reject(
                f"连接失败：{exc}\r\n"
                "请检查主机地址/端口/账号是否正确，以及该主机的 ~/.ssh/authorized_keys "
                "是否已包含系统的全局公钥。"
            )
            return

        prune_text()
        SSHSession.objects.filter(pk=self.audit_id).update(host_label=f'{host.name} ({host.ip_addr}:{host.port})'[:200])
        self.recorder = TextRecorder(self.audit_id)
        self.send('\r\n本会话保存脱敏后的终端输出文本（最多 256 KiB，保留 30 天或最近 1000 个会话）；不记录原始键盘输入，请勿在终端显示凭据。\r\n')
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
        self._cleanup()

    def _read_loop(self):
        """持续读取 SSH 输出并推送到浏览器。"""
        try:
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            window, sent = time.monotonic(), 0
            while not self.stop_event.is_set():
                if not self._still_authorized():
                    self.send('\r\n会话授权已撤回。\r\n')
                    break
                if time.monotonic() - self.last_input > 900 or time.time() >= self.token_expires:
                    self.send('\r\n会话已过期或空闲超过 15 分钟，请重新认证连接。\r\n')
                    break
                try:
                    data = self.ssh_chan.recv(16384)
                except socket.timeout:
                    continue
                if not data:
                    break
                elapsed = time.monotonic() - window
                if elapsed >= 1:
                    window, sent = time.monotonic(), 0
                elif sent + len(data) > 131072:
                    if self.stop_event.wait(1-elapsed):
                        break
                    window, sent = time.monotonic(), 0
                sent += len(data)
                text = decoder.decode(data)
                self.send(text)
                try:
                    self.recorder.append(text)
                except Exception:
                    logger.exception('会话文本写入失败 session=%s', self.audit_id)
        except Exception:  # noqa: BLE001 - 通道关闭属于正常情况
            pass
        finally:
            try:
                if self.recorder:
                    self.recorder.flush(final=True)
                SSHSession.objects.filter(pk=self.audit_id, ended_at__isnull=True).update(status='closed', ended_at=timezone.now())
            except Exception:
                logger.exception('会话文本结束保存失败 session=%s', self.audit_id)
            try:
                self.close()
            except Exception:  # noqa: BLE001
                pass
            self._cleanup()
            close_old_connections()

    def receive(self, text_data=None, bytes_data=None):
        if self.ssh_chan is None:
            return
        if self.audit_id and not self._still_authorized():
            self.close(code=4403)
            self._cleanup()
            return
        payload = text_data if text_data is not None else (bytes_data or b"").decode("utf-8", "ignore")
        if len(payload) > 32768:
            self._reject('单次输入过大，请通过文件传输上传脚本')
            return
        try:
            try:
                control = json.loads(payload)
            except (ValueError, TypeError):
                control = None
            if isinstance(control, dict) and control.get('type') in ('ping', 'resize', 'input'):
                if control['type'] == 'ping':
                    return
                if control['type'] == 'resize':
                    cols, rows = control.get('cols'), control.get('rows')
                    if type(cols) is int and type(rows) is int and 10 <= cols <= 500 and 2 <= rows <= 200:
                        self.ssh_chan.resize_pty(width=cols, height=rows)
                    return
                payload = control.get('data', '')
                if not isinstance(payload, str):
                    return
            self.last_input = time.monotonic()
            self.ssh_chan.send(payload)
        except Exception:  # noqa: BLE001
            self.close()

    def disconnect(self, code):
        if self.audit_id:
            SSHSession.objects.filter(pk=self.audit_id, ended_at__isnull=True).update(status="closed", ended_at=timezone.now())
        self._cleanup()

    def _cleanup(self):
        self.stop_event.set()
        if self.ssh_chan is not None:
            try:
                self.ssh_chan.close()
            except Exception:  # noqa: BLE001
                pass
        with self.cleanup_lock:
            if self.slot_acquired:
                self.slot_acquired = False
                _terminal_slots.release()
        if self.ssh is not None and self.ssh.client is not None:
            try:
                self.ssh.client.close()
            except Exception:  # noqa: BLE001
                pass
