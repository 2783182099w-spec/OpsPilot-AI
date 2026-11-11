from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mtask.managed import validate_definition
from mtask.models import ManagedJob, ManagedJobRevision


EXAMPLES = [
    ('Linux 健康快照', 'shell', '只读查看内核、运行时长、内存与根分区空间。', [], '''
set -eu
printf '=== Kernel ===\n'
uname -sr
printf '\n=== Uptime ===\n'
uptime
printf '\n=== Memory ===\n'
free -m
printf '\n=== Root filesystem ===\n'
df -h /
'''),
    ('systemd 服务状态', 'shell', '查看单个服务的加载状态、运行状态、退出原因与重启次数。', [
        {'name':'service','label':'服务','type':'enum','choices':['nginx.service','sshd.service','ssh.service','docker.service'],'default':'nginx.service'}
    ], '''
set -eu
systemctl show --no-pager --property=LoadState,ActiveState,SubState,Result,NRestarts -- "$1"
'''),
    ('主机监听端口盘点', 'shell', '列出本机正在监听的 TCP/UDP 套接字，最多显示 100 行。', [], '''
set -euo pipefail
ss -lntu | awk 'NR <= 100 { print } END { if (NR > 100) print "(truncated)" }'
'''),
    ('根分区容量与 inode 复核', 'python', '用 Python 核对根分区剩余空间与 inode，返回简洁的 JSON 证据。', [], '''
import json
import os
import shutil

disk = shutil.disk_usage("/")
stats = os.statvfs("/")
print(json.dumps({
    "path": "/",
    "total_gib": round(disk.total / 1024**3, 2),
    "available_gib": round(disk.free / 1024**3, 2),
    "used_percent": round(100 * disk.used / disk.total, 1),
    "inode_available": stats.f_favail,
    "inode_total": stats.f_files,
}, indent=2))
'''),
]


class Command(BaseCommand):
    help = '安装四个只读示例到作业库，保留已存在或编辑过的作业'

    def add_arguments(self, parser):
        parser.add_argument('--owner', required=True, help='用于归属和版本审计的管理员用户名')

    @transaction.atomic
    def handle(self, *args, **options):
        user = get_user_model().objects.select_for_update().filter(
            username=options['owner'],is_active=True,is_superuser=True).first()
        if not user:
            raise CommandError('请选择已启用的管理员')
        seen = {definition.get('name') for definition in ManagedJob.objects.values_list('definition',flat=True)}
        created = 0
        for name,kind,description,parameters,source in EXAMPLES:
            if name in seen:
                continue
            if len(seen) + created >= 200:
                raise CommandError('作业库容量不足')
            definition = validate_definition({'name':name,'kind':kind,'description':description,
                                              'source':source.strip()+'\n','risk':'read_only',
                                              'environments':['test','staging','other'],
                                              'parameters':parameters,'timeout':30,'concurrency':2})
            job = ManagedJob.objects.create(owner=user,definition=definition,state='published')
            ManagedJobRevision.objects.create(job=job,actor=user,definition=definition,version=1,state='published')
            created += 1
        self.stdout.write(self.style.SUCCESS(f'已安装 {created} 条只读作业'))
