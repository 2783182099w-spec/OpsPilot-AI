from contextlib import contextmanager
from pathlib import Path
from django.conf import settings
from django.core.management.base import CommandError


@contextmanager
def worker_lock():
    path = Path(settings.MONITOR_CONFIG_DIR)/'monitorworker.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    stream = path.open('a+b')
    try:
        stream.seek(0,2)
        if stream.tell()==0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if __import__('os').name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise CommandError('该配置目录已有监控同步器，拒绝重复运行') from exc
        yield
    finally:
        # Closing releases the OS lock, including after process termination.
        stream.close()
