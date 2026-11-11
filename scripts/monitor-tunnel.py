"""Local test tunnel; forwards only localhost:19100 to remote localhost:9100."""
import argparse
import os
import sys
import socketserver
import select
import threading
import time
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
parser.add_argument('--port',type=int,default=19100)
args=parser.parse_args()
slots=threading.BoundedSemaphore(8)
transport=None

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not slots.acquire(blocking=False):
            return
        channel=None
        try:
            current=transport
            if not current or not current.is_active():
                return
            channel=current.open_channel('direct-tcpip',('127.0.0.1',9100),self.client_address,timeout=10)
            self.request.settimeout(15)
            channel.settimeout(15)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                ready,_,_=select.select([self.request,channel],[],[],1)
                for source in ready:
                    data=source.recv(32768)
                    if not data:
                        return
                    (channel if source is self.request else self.request).sendall(data)
        except Exception:
            pass
        finally:
            if channel:
                channel.close()
            slots.release()

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

with Server(('127.0.0.1',args.port),Handler) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        while True:
            ssh=None
            try:
                host=Host.objects.get(pk=args.host_id,is_deleted=False)
                ssh=SSHParamiko(host.ip_addr,host.port,host.username,PkeyManager.get(settings.DEFAULT_KEY_NAME)[0])
                if not ssh.get_connected_client():
                    raise RuntimeError('Trusted SSH tunnel connection failed')
                transport=ssh.client.get_transport()
                transport.set_keepalive(30)
                print('Exporter tunnel connected',flush=True)
                while transport.is_active():
                    time.sleep(2)
            except Exception as exc:
                print(type(exc).__name__+': exporter tunnel unavailable; retrying',flush=True)
            finally:
                transport=None
                if ssh:
                    ssh.close()
            time.sleep(5)
    except KeyboardInterrupt:
        server.shutdown()
