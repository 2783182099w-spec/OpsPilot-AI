"""Explicit, scoped deployment of a loopback-only, read-only exporter."""
import argparse
import hashlib
import json
import os
import shlex
import sys
import uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'opspilot_api'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','opspilot_api.settings.local')
import django
django.setup()
from django.conf import settings
from host.models import Host
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko
parser=argparse.ArgumentParser()
parser.add_argument('--host-id',type=int,required=True)
parser.add_argument('--confirmed',action='store_true',required=True)
args=parser.parse_args()
artifact=json.loads((root/'.runtime/monitor-artifacts.json').read_text())['node_exporter']
archive=root/'.runtime/downloads'/artifact['filename']
if hashlib.sha256(archive.read_bytes()).hexdigest()!=artifact['sha256']:
    raise RuntimeError('Local archive checksum mismatch')
host=Host.objects.get(pk=args.host_id,is_deleted=False)
ssh=SSHParamiko(host.ip_addr,host.port,host.username,PkeyManager.get(settings.DEFAULT_KEY_NAME)[0])
remote='/var/tmp/opspilot-exporter-'+uuid.uuid4().hex+'.tar.gz'
try:
    if ssh.get_connected_client() is None:
        raise RuntimeError(ssh.error_message)
    code,output=ssh.execute_cmd('uname -m; test ! -e /etc/systemd/system/opspilot-node-exporter.service; test ! -e /opt/opspilot-node-exporter; test -z "$(ss -ltnH \'( sport = :9100 )\')"',timeout=15)
    if code or output.strip()!='x86_64':
        raise RuntimeError('Preflight refused: architecture, existing path/service or occupied port; no files changed')
    sftp=ssh.client.open_sftp()
    try:
        sftp.put(str(archive),remote)
    finally:
        sftp.close()
    unit='[Unit]\nDescription=OpsPilot Node Exporter (loopback only)\nAfter=network.target\n[Service]\nDynamicUser=yes\nExecStart=/opt/opspilot-node-exporter/node_exporter --web.listen-address=127.0.0.1:9100 --web.disable-exporter-metrics --collector.disable-defaults --collector.cpu --collector.meminfo --collector.filesystem --collector.loadavg --collector.netdev --collector.stat --collector.uname --collector.time\nRestart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nProtectKernelTunables=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\nRestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\nMemoryMax=96M\nCPUQuota=10%\n[Install]\nWantedBy=multi-user.target\n'
    folder=artifact['filename'].removesuffix('.tar.gz')
    command=f"echo {shlex.quote(artifact['sha256']+'  '+remote)} | sha256sum -c -\ninstall -d -m 755 /opt/opspilot-node-exporter\ntar -xOf {shlex.quote(remote)} {shlex.quote(folder+'/node_exporter')} > /opt/opspilot-node-exporter/node_exporter\nchmod 755 /opt/opspilot-node-exporter/node_exporter\nprintf %s {shlex.quote(unit)} > /etc/systemd/system/opspilot-node-exporter.service\nsystemctl daemon-reload\nsystemctl enable --now opspilot-node-exporter.service\nsleep 2\nsystemctl is-active opspilot-node-exporter.service\n/opt/opspilot-node-exporter/node_exporter --version 2>&1 | head -n 1\nss -ltn '( sport = :9100 )'\ncurl -fsS --max-time 10 http://127.0.0.1:9100/metrics | grep -E '^node_(memory_MemTotal_bytes|load1|boot_time_seconds)'\nsystemctl show opspilot-node-exporter -p MemoryCurrent -p CPUUsageNSec"
    code,output=ssh.execute_cmd(command,timeout=45,max_output=8192)
    print(output)
    if code:
        raise RuntimeError('Exporter deployment requires inspection; existing business services were not changed')
    (root/'.runtime/monitor-test.json').write_text(json.dumps({'host_id':host.id,'local_port':19100,'version':artifact['version'],'sha256':artifact['sha256']},indent=2),encoding='utf-8')
finally:
    if ssh.client:
        try:
            sftp=ssh.client.open_sftp()
            sftp.remove(remote)
            sftp.close()
        except Exception:
            pass
    ssh.close()
