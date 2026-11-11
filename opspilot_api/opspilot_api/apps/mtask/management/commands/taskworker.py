import time
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from mtask.engine import acquire_lease, heartbeat, release_lease, claim_task, dispatch_schedules
from mtask.tasks import run_task
from mtask.diagnostic_workflows import dispatch as dispatch_diagnostics

class Command(BaseCommand):
    help = '轻量单执行器，数据库租约；并发上限 4；不需要 Redis'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='仅处理一次，供本地测试使用')

    def handle(self, *args, **options):
        token = None
        try:
            # 崩溃后等旧租约自然到期；不覆盖活跃执行器。
            for attempt in range(17):
                token = acquire_lease()
                if token:
                    break
                if options['once']:
                    raise CommandError('已有活动执行器')
                time.sleep(2)
            if not token:
                raise CommandError('已有活动执行器，禁止重复启动')
            next_diagnostics = 0
            while True:
                close_old_connections()
                if not heartbeat(token):
                    break
                dispatch_schedules()
                if time.monotonic() >= next_diagnostics:
                    dispatch_diagnostics()
                    next_diagnostics = time.monotonic()+10
                task = claim_task(token)
                if task:
                    run_task(task)
                if options['once']:
                    break
                if not task:
                    time.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            if token:
                release_lease(token)
