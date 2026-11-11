import hashlib
import os
import stat
import tempfile
import time
import uuid
from pathlib import PurePosixPath
from threading import BoundedSemaphore
from itertools import islice

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import FileTransfer
from .permissions import accessible_hosts
from users.access import is_observer
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko

_slots = BoundedSemaphore(2)
MAX_BYTES = 10 * 1024 * 1024


def checked_path(sftp, path, write=False):
    if not isinstance(path, str) or len(path) > 1000 or not path.startswith('/') or '..' in path.split('/') or any(c in path for c in ('\x00','\r','\n')):
        raise ValidationError('必须使用不含 .. 的绝对路径')
    roots = getattr(settings, 'SFTP_WRITE_ROOTS' if write else 'SFTP_READ_ROOTS', ['/tmp'] if write else ['/tmp','/var/log'])
    real = sftp.normalize(str(PurePosixPath(path)))
    if not any(real == root.rstrip('/') or real.startswith(root.rstrip('/') + '/') for root in roots):
        raise ValidationError('路径不在允许目录内，或符号链接指向允许范围之外')
    return real


class FilesView(APIView):
    def get(self, request, pk):
        return self.transfer(request, pk, False)

    def post(self, request, pk):
        return self.transfer(request, pk, True)

    def transfer(self, request, pk, upload):
        host = get_object_or_404(accessible_hosts(request.user), pk=pk)
        if not upload and request.query_params.get('history') == '1':
            records = FileTransfer.objects.filter(host=host)
            if not request.user.is_superuser:
                records = records.filter(owner=request.user)
            return Response(list(records.order_by('-id').values('id','direction','path','size','sha256','status','created_at')[:50]))
        if is_observer(request.user):
            raise PermissionDenied("只读观察员不能连接 SFTP")
        if not _slots.acquire(blocking=False):
            return Response({'error': '最多同时传输两个文件，请稍后重试'}, status=429)
        ssh, sftp, temporary, remote_temp = None, None, None, None
        record = None
        try:
            key, _ = PkeyManager.get(settings.DEFAULT_KEY_NAME)
            ssh = SSHParamiko(host.ip_addr, host.port, host.username, key)
            if ssh.get_connected_client() is None:
                raise ValidationError(ssh.error_message or 'SSH 连接失败')
            sftp = ssh.client.open_sftp()
            sftp.get_channel().settimeout(10)
            path = checked_path(sftp, request.data.get('path', '/tmp') if upload else request.query_params.get('path', '/tmp'), upload)
            deadline = time.monotonic() + 60
            if not upload and request.query_params.get('download') != '1':
                entries = list(islice(sftp.listdir_iter(path, read_aheads=1), 201))
                return Response({'path': path, 'truncated': len(entries) > 200, 'entries': [{'name': item.filename, 'size': item.st_size, 'directory': stat.S_ISDIR(item.st_mode), 'symlink': stat.S_ISLNK(item.st_mode)} for item in entries[:200]]})
            record = FileTransfer.objects.create(owner=request.user, host=host, direction='upload' if upload else 'download', path=path)
            digest = hashlib.sha256()
            size = 0
            if upload:
                file = request.FILES.get('file')
                if not file or file.size > MAX_BYTES:
                    raise ValidationError('请选择不超过 10MB 的文件')
                filename = PurePosixPath(file.name).name
                if filename in ('', '.', '..') or any(c in filename for c in ('\x00','\r','\n')):
                    raise ValidationError('文件名不合法')
                target = path.rstrip('/') + '/' + filename
                try:
                    existing = sftp.lstat(target)
                except FileNotFoundError:
                    existing = None
                if existing and (not stat.S_ISREG(existing.st_mode) or request.data.get('overwrite') != 'true'):
                    raise ValidationError('目标已存在；请明确确认覆盖，目录和符号链接不允许覆盖')
                remote_temp = path.rstrip('/') + '/.opspilot-' + uuid.uuid4().hex
                with sftp.open(remote_temp, 'wx') as destination:
                    sftp.chmod(remote_temp, 0o600)
                    for chunk in file.chunks(65536):
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise ValidationError('文件超限或传输超时')
                        destination.write(chunk)
                        digest.update(chunk)
                if existing:
                    sftp.posix_rename(remote_temp, target)
                else:
                    sftp.rename(remote_temp, target)
                remote_temp = None
                record.path = target
                response = Response({'message': '文件已上传', 'sha256': digest.hexdigest(), 'size': size})
            else:
                info = sftp.lstat(path)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise ValidationError('只允许下载不超过 10MB 的普通文件')
                temporary = tempfile.SpooledTemporaryFile(max_size=512*1024)
                with sftp.open(path, 'rb') as source:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise ValidationError('文件超限或传输超时')
                        temporary.write(chunk)
                        digest.update(chunk)
                after = sftp.stat(path)
                if size != info.st_size or after.st_mtime != info.st_mtime or after.st_size != info.st_size:
                    raise ValidationError('文件在传输期间发生变化，请先固定日志片段')
                temporary.seek(0)
                response = FileResponse(temporary, as_attachment=True, filename=PurePosixPath(path).name)
                response['X-Content-SHA256'] = digest.hexdigest()
                temporary = None  # FileResponse 负责关闭
            record.size, record.sha256, record.status = size, digest.hexdigest(), 'success'
            record.save()
            return response
        except ValidationError:
            raise
        except Exception:
            raise ValidationError('文件传输失败，请检查 SSH 指纹、登录权限、目录权限及 SFTP 支持情况')
        finally:
            if remote_temp and sftp:
                try:
                    sftp.remove(remote_temp)
                except Exception:
                    pass
            if temporary:
                temporary.close()
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            if ssh:
                ssh.close()
            _slots.release()
