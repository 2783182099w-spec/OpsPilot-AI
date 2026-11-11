"""Remote deploy script source. Runs through the existing managed SSH executor."""
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


def command(*args):
    subprocess.run(args, check=True, timeout=18, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fetch(url, destination, expected):
    endpoint = urlsplit(url)
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password:
        raise ValueError('HTTPS artifact URL required')
    connection = http.client.HTTPSConnection(endpoint.hostname, endpoint.port or 443, timeout=8,
                                              context=ssl.create_default_context())
    digest = sha256()
    total = 0
    try:
        connection.request('GET', endpoint.path)
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError('Artifact server must return HTTP 200 without redirects')
            deadline = time.monotonic() + 75
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > 100 * 1024 * 1024 or time.monotonic() > deadline:
                    raise ValueError('Artifact exceeds download limit')
                digest.update(chunk)
                destination.write(chunk)
        if total == 0 or digest.hexdigest() != expected:
            raise ValueError('Artifact SHA-256 mismatch')
    finally:
        connection.close()


def healthy(port, path):
    for attempt in range(3):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', path)
            with connection.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f'HTTP health check returned {response.status}')
        finally:
            connection.close()
        print(f'HTTP health check {attempt + 1}/3 passed', flush=True)
        if attempt < 2:
            time.sleep(2)


def main(config):
    root = Path('/opt/opspilot-releases') / config['application']
    current = root / 'current'
    if not root.is_dir() or root.resolve() != root or (current.exists() and not current.is_symlink()):
        raise ValueError('Prepare the release directory and current symlink before deployment')
    unit = config['unit']
    fields = subprocess.run(['systemctl','show','--property=LoadState,ExecStart','--',unit],
                            check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            universal_newlines=True,timeout=10)
    if ('LoadState=loaded' not in fields.stdout.splitlines()
            or not re.search(r'(?m)^ExecStart=\{ path=' + re.escape(str(current)) + r'\s*;', fields.stdout)):
        raise ValueError('Unit must be loaded with ExecStart pointing directly at the application current path')
    executable = root / config['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', executable.name):
        raise ValueError('Invalid digest')
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=root)
    link = Path(temporary + '.link')
    try:
        with os.fdopen(fd, 'wb') as handle:
            fetch(config['url'], handle, config['sha256'])
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o755)
        print('Artifact digest verified', flush=True)
        os.replace(temporary, executable)
        link.symlink_to(executable.name)
        os.replace(link, current)
        print('Current artifact switched; restarting service', flush=True)
        command('systemctl', 'restart', '--', config['unit'])
        command('systemctl', 'is-active', '--quiet', '--', config['unit'])
        healthy(config['port'], config['path'])
    finally:
        for path in (Path(temporary), link):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    try:
        main(json.loads(sys.argv[1]))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, http.client.HTTPException) as exc:
        print(f'{type(exc).__name__}: {exc}', flush=True)
        sys.exit(1)
