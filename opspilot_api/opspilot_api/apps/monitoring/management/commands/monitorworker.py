import time
import json
import hashlib
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from host.models import Finding
from monitoring.locking import worker_lock
from monitoring.models import MonitorTarget, MonitorRule, MonitorHealth
from monitoring.configuration import write_config, defaults
from monitoring.prometheus import api, Unavailable
from monitoring.sync import sync_alerts
from monitoring.service_checks import sync_service_checks


class Command(BaseCommand):
    help = '每 30 秒应用监控配置并同步 Prometheus 告警，不发送通知'

    def handle(self, *args, **options):
        with worker_lock():
            self.run_worker()

    def run_worker(self):
        defaults()
        fingerprint = ''
        try:
            while True:
                close_old_connections()
                health,_ = MonitorHealth.objects.get_or_create(name='default')
                value = json.dumps([list(MonitorTarget.objects.filter(host__is_deleted=False).values()),list(MonitorRule.objects.values())],default=str,sort_keys=True)
                signature = hashlib.sha256(value.encode()).hexdigest()
                if signature != fingerprint:
                    try:
                        write_config()
                        api('/-/reload',post=True)
                        fingerprint = signature
                        health.config_error = ''
                    except (Unavailable,OSError) as exc:
                        health.config_error = ('配置暂时不可写，将自动重试；请检查目录权限或文件占用' if isinstance(exc,OSError) else '配置未应用：'+str(exc))[:500]
                    health.save(update_fields=['config_error'])
                if health.config_error:
                    health.heartbeat_at = timezone.now()
                    health.save(update_fields=['heartbeat_at'])
                    Finding.objects.filter(source='monitor').exclude(condition='recovered').update(condition='unknown')
                else:
                    sync_alerts()
                sync_service_checks()
                time.sleep(30)
        except KeyboardInterrupt:
            pass
