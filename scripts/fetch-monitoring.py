"""Download official release artifacts with independent checksum file verification."""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
import requests

parser=argparse.ArgumentParser()
parser.add_argument('--proxy',default=None)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]/'.runtime'/'downloads'
root.mkdir(parents=True,exist_ok=True)
session=requests.Session()
session.trust_env=False
if args.proxy:
    session.proxies={'https':args.proxy,'http':args.proxy}
receipt={}
for repo,filename in [('node_exporter','node_exporter-1.12.1.linux-amd64.tar.gz'),('prometheus','prometheus-3.15.1.windows-amd64.zip')]:
    version=re.search(r'-(\d+\.\d+\.\d+)\.', filename).group(1)
    base=f'https://github.com/prometheus/{repo}/releases/download/v{version}/'
    checks=session.get(base+'sha256sums.txt',timeout=30)
    checks.raise_for_status()
    expected=next(line.split()[0] for line in checks.text.splitlines() if line.split()[-1].lstrip('*')==filename)
    target=root/filename
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
        temporary=target.with_suffix('.partial')
        with session.get(base+filename,timeout=(15,60),stream=True) as response:
            response.raise_for_status()
            size=0
            with temporary.open('wb') as output:
                for chunk in response.iter_content(1024*1024):
                    size+=len(chunk)
                    if size>200*1024*1024:
                        raise RuntimeError('Artifact exceeds 200 MiB')
                    output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Official checksum mismatch')
        temporary.replace(target)
    if repo=='prometheus':
        out=root.parent/'prometheus'/'bin'
        out.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(target) as archive:
            for name in ('prometheus.exe','promtool.exe'):
                item=next(i for i in archive.infolist() if i.filename.rsplit('/',1)[-1]==name)
                (out/name).write_bytes(archive.read(item))
    receipt[repo]={'version':version,'filename':filename,'sha256':expected,'source':base+filename}
    print(repo,version,'SHA256 verified',flush=True)
(root.parent/'monitor-artifacts.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
