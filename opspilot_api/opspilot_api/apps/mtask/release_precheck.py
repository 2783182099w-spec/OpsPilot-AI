"""Read-only SSH precheck for an already registered systemd application."""
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def check(config):
    root = Path('/opt/opspilot-releases') / config['application']
    current = root / 'current'
    tools = all(shutil.which(name) for name in ('systemctl','timeout','sha256sum'))
    print('执行工具：' + ('齐备' if tools else '缺少 systemctl、timeout 或 sha256sum'), flush=True)
    directory = root.is_dir() and root.resolve() == root and os.access(str(root),os.W_OK|os.X_OK)
    print('发布目录：' + ('存在且当前账号可写' if directory else '缺失、不可写或含符号链接'), flush=True)
    link = not current.exists() or current.is_symlink()
    print('current 路径：' + ('可用于切换' if link else '已被普通文件或目录占用'), flush=True)
    unit_ok = False
    if tools:
        try:
            result = subprocess.run(['systemctl','show','--property=LoadState,ExecStart','--',config['unit']],
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=8)
            unit_ok = result.returncode == 0 and 'LoadState=loaded' in result.stdout.splitlines() and bool(
                re.search(r'(?m)^ExecStart=\{ path=' + re.escape(str(current)) + r'\s*;',result.stdout))
        except (OSError,subprocess.SubprocessError):
            pass
    print('systemd 单元：' + ('已加载且 ExecStart 匹配' if unit_ok else '未加载或 ExecStart 未指向 current'), flush=True)
    print('服务重启权限将在正式发布时验证。', flush=True)
    connection = http.client.HTTPConnection('127.0.0.1',config['port'],timeout=3)
    try:
        connection.request('GET',config['path'])
        with connection.getresponse() as response:
            print('当前健康接口：HTTP {}'.format(response.status),flush=True)
    except (OSError,http.client.HTTPException):
        print('当前健康接口未响应，首次发布前可能尚未启动。',flush=True)
    finally:
        connection.close()
    return 0 if tools and directory and link and unit_ok else 1


if __name__ == '__main__':
    sys.exit(check(json.loads(sys.argv[1])))
