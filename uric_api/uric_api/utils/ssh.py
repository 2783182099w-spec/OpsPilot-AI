"""基于 paramiko 的 SSH 连接封装。"""

from io import StringIO

from paramiko import AutoAddPolicy, RSAKey, SSHClient
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
        client.set_missing_host_key_policy(AutoAddPolicy())
        try:
            client.connect(**self.params)
        except (TimeoutError, NoValidConnectionsError, AuthenticationException, SSHException) as exc:
            self.error_message = str(exc) or exc.__class__.__name__
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

    def execute_cmd(self, cmd, timeout=1800):
        """执行命令，返回 (退出码, 输出)。"""
        cmd = "set -e\n" + cmd
        channel = self.client.get_transport().open_session()
        channel.settimeout(timeout)
        channel.set_combine_stderr(True)
        channel.exec_command(cmd)
        try:
            output = channel.makefile("rb", -1).read().decode()
        except UnicodeDecodeError:
            output = channel.makefile("rb", -1).read().decode("GBK")
        return channel.recv_exit_status(), output
