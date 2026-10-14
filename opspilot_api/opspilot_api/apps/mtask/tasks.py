from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from host.permissions import require_hosts
from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.redaction import redact
from opspilot_api.utils.ssh import SSHParamiko
from .models import ExecutionTask


def run_task(task):
    results = []
    try:
        hosts = require_hosts(task.owner, task.host_ids)
        private_key, _ = PkeyManager.get(settings.DEFAULT_KEY_NAME)

        def execute(host):
            ssh = None
            result = {"host_info": {"id": host.id, "name": host.name, "ip_addr": host.ip_addr, "port": host.port}, "res_code": None, "res_data": ""}
            try:
                ssh = SSHParamiko(host.ip_addr, host.port, host.username, private_key)
                if ssh.get_connected_client() is None:
                    raise ConnectionError("SSH 连接或指纹验证失败")
                code, output = ssh.execute_cmd(task.command, timeout=60)
                result.update(res_code=code, res_data=redact(output))
            except Exception as exc:
                result["res_data"] = redact(str(exc))
            finally:
                if ssh:
                    ssh.close()
                close_old_connections()
            return result

        with ThreadPoolExecutor(max_workers=4) as pool:
            for future in as_completed([pool.submit(execute, host) for host in hosts]):
                results.append(future.result())
                ExecutionTask.objects.filter(pk=task.pk).update(results=results)
        status = "completed" if all(item["res_code"] == 0 for item in results) else "failed"
    except Exception as exc:
        results.append({"error": redact(str(exc))})
        status = "failed"
    ExecutionTask.objects.filter(pk=task.pk).update(status=status, results=results, finished_at=timezone.now())
