"""验证本地运行服务，临时账号与资产仅用于本脚本，完成后清理。"""
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'opspilot_api'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'opspilot_api.settings.local')
import django
django.setup()
import requests
from django.contrib.auth import get_user_model
from host.models import Host

name = 'smoke_' + secrets.token_hex(8)
password = secrets.token_urlsafe(32)
user = get_user_model().objects.create_user(name, password=password)
client = requests.Session()
client.trust_env = False
host_id = None
try:
    assert client.get('http://127.0.0.1:9000', timeout=10).status_code == 200
    assert client.get('http://127.0.0.1:8000/home/', timeout=10).status_code == 200
    assert client.get('http://127.0.0.1:8000/static/admin/css/base.css', timeout=10).status_code == 200
    assert client.get('http://127.0.0.1:8000/host/', timeout=10).status_code == 401
    login = client.post('http://127.0.0.1:8000/users/login/', json={'username': name, 'password': password}, timeout=10)
    assert login.status_code == 200
    client.headers['Authorization'] = 'jwt ' + login.json()['access']
    assert client.get('http://127.0.0.1:8000/users/me/', timeout=10).json()['username'] == name
    asset = client.post('http://127.0.0.1:8000/host/', json={'name': name, 'ip_addr': '127.0.0.1', 'port': 22222, 'username': 'smoke'}, timeout=10)
    assert asset.status_code == 201, asset.status_code
    host_id = asset.json()['id']
    assert client.patch(f'http://127.0.0.1:8000/host/{host_id}', json={'tags': ['smoke'], 'asset_status': 'maintenance'}, timeout=10).status_code == 200
    listing = client.get('http://127.0.0.1:8000/host/', params={'page': 1, 'search': name}, timeout=10)
    assert listing.json()['count'] == 1
    assert client.get('http://127.0.0.1:8000/host/export', timeout=10).status_code == 200
    assert client.get('http://127.0.0.1:8000/mtask/tasks', timeout=10).status_code == 200
    assert client.get('http://127.0.0.1:8000/ai/history', timeout=10).status_code == 200
    assert client.delete(f'http://127.0.0.1:8000/host/{host_id}', timeout=10).status_code == 204
    print('PASS: 页面、健康检查、未登录拒绝、JWT 登录、资产新增/编辑/分页/导出/删除、任务历史、AI 调用历史')
finally:
    if host_id:
        Host.objects.filter(pk=host_id, users=user).delete()
    user.delete()
    client.close()
