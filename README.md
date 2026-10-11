# OpsPilot-AI

一个轻量运维平台，包含主机资产管理、Web SSH 终端、指令模板批量执行和 AI 助手。

## 技术栈

- 后端：Django 3.2 + Django REST Framework + Channels（WebSocket）+ paramiko
- 前端：Vue 3 + Vue Router + Vuex + Ant Design Vue 4 + xterm.js + ECharts
- 数据库：开发用 SQLite（`settings/local.py`），也可切换 MySQL

## 目录结构

```
uric_api/                 # Django 后端
  uric_api/apps/
    host/                 # 资产管理 + Web SSH 终端
    mtask/                # 指令模板 + 批量执行
    users/                # 用户与 JWT 登录
    ai/                   # DeepSeek AI 助手
    home/                 # 健康检查
  uric_api/settings/      # dev / prod / local 配置
uric_web/                 # Vue 前端
  src/views/              # 展示中心 / 资产管理 / Console / 批量执行 / AI 助手
  src/api.js              # 统一的 axios 客户端（自动带 JWT、统一错误处理）
```

## 快速开始

### 1. 后端

```bash
cd uric_api
python -m venv ../venv
../venv/Scripts/pip install -r requirements.txt   # Windows
# source ../venv/bin/activate && pip install -r requirements.txt   # Linux/macOS

export DJANGO_SETTINGS_MODULE=uric_api.settings.local
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

### 2. 前端

```bash
cd uric_web
pnpm install          # 或 npm install
pnpm run serve        # 默认 http://127.0.0.1:9000
```

默认后端地址在前端 `src/settings.js` 中配置，登录接口为 `POST /users/login/`。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | 选用的配置模块，本地开发用 `uric_api.settings.local` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key，仅后端使用，不要写进前端 |
| `DEEPSEEK_MODEL` | 模型名，默认 `deepseek-flash` |
| `DJANGO_SECRET_KEY` / `DJANGO_ALLOWED_HOSTS` / `DJANGO_CORS_ORIGINS` | 生产环境（`settings/prod.py`）使用 |

敏感信息可以写在 `uric_api/uric_api/settings/local_secret.py`，该文件已被 `.gitignore` 忽略。

## SSH 免密说明

新建主机时后端会校验连接并生成全局 RSA 密钥对，同时把公钥写入目标主机的
`~/.ssh/authorized_keys`，之后 Web SSH 终端与批量执行都靠这对密钥免密登录。

- `settings/local.py` 中 `HOST_SSH_VALIDATE = False` 时，新建主机不会强制校验连通性，
  也不会下发公钥；此时可在「资产管理」中点击「同步密钥」补装公钥。
- `HOST_SSH_VALIDATE = True`（默认）时会严格校验：账号密码错误会直接报错。
