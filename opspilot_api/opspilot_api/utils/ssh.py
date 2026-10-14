"""基于 paramiko 的 SSH 连接封装。"""

from io import StringIO

from paramiko import RejectPolicy, RSAKey, SSHClient
from django.conf import settings
import time
from pathlib import Path
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
    SSHException,
)


class SSHParamiko:
    """提供连接检测、命令执行与公钥下发能力。"""

    def __init__(self, hostname, port=22, username="root", pkey=None, password=None, connect_timeout=6):
        if pkey is None and password is None:
            raise SSHException("私钥或者密码必须选择传入一个")

        self.client = None
        self.error_message = None
        self.params = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "password": password,
            "pkey": RSAKey.from_private_key(StringIO(pkey)) if isinstance(pkey, str) else pkey,
            # TCP 连接超时；banner/auth 单独放大，避免慢速服务器被误判为认证失败
            "timeout": connect_timeout,
            "banner_timeout": 15,
            "auth_timeout": 30,
        }

    def get_connected_client(self):
        """建立连接；失败时返回 None，并把失败原因记录在 error_message。"""
        if self.client is not None:
            return self.client

        client = SSHClient()
        client.load_system_host_keys()
        known_hosts = getattr(settings, "SSH_KNOWN_HOSTS", "")
        if known_hosts and Path(known_hosts).is_file():
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(RejectPolicy())
        try:
            client.connect(**self.params, allow_agent=False, look_for_keys=False)
        except (TimeoutError, NoValidConnectionsError, AuthenticationException, SSHException) as exc:
            client.close()
            self.error_message = str(exc) or exc.__class__.__name__
            if "not found in known_hosts" in self.error_message:
                self.error_message = "尚未登记该主机的 SSH 指纹，请管理员先在资产管理中登记可信主机公钥；当前尚未进行密码校验"
            return None

        self.client = client
        return self.client

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    @staticmethod
    def gen_key():
        """生成 RSA 公私钥对，返回 (私钥, 公钥)。"""
        iodata = StringIO()
        key = RSAKey.generate(2048)
        key.write_private_key(iodata)
        return iodata.getvalue(), "ssh-rsa " + key.get_base64()

    def upload_key(self, public_key):
        """把公钥写入目标主机的 authorized_keys（重复执行不会产生重复行）。"""
        cmd = (
            "mkdir -p -m 700 ~/.ssh && "
            f"(grep -qxF {public_key!r} ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo {public_key!r} >> ~/.ssh/authorized_keys) && "
            "chmod 600 ~/.ssh/authorized_keys"
        )
        code, output = self.execute_cmd(cmd)
        if code != 0:
            raise RuntimeError(f"添加公钥失败: {output}")

    def execute_cmd(self, cmd, timeout=60):
        """执行命令，返回 (退出码, 输出)。"""
        cmd = "set -e\n" + cmd
        channel = self.client.get_transport().open_session(timeout=10)
        channel.settimeout(timeout)
        channel.set_combine_stderr(True)
        channel.exec_command(cmd)
        deadline = time.monotonic() + timeout
        output = bytearray()
        try:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("命令执行超时")
                if channel.recv_ready():
                    output.extend(channel.recv(32768))
                    if len(output) > 1024 * 1024:
                        raise ValueError("命令输出超过 1MB")
                elif channel.exit_status_ready():
                    return channel.recv_exit_status(), output.decode("utf-8", errors="replace")
                else:
                    time.sleep(0.02)
        finally:
            channel.close()
