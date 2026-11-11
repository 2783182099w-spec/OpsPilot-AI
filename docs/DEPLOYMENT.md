# 本地运行与 Alibaba Cloud Linux 3 容器部署

## 当前交付与边界

后端沿用现有 Django 应用，新增任务记录、审批、单实例执行器与 AI 调用记录。
任务执行器每个任务最多 4 个 SSH 连接，单台命令限时 60 秒、输出上限 1MB。
普通用户只能访问明确授权的主机，新增资产自动授权给创建者；管理员可通过 Django admin 调整 Host.users。
普通用户命令必须经过管理员审批；管理员自己提交的任务直接进入队列。
旧同步命令接口仅允许管理员使用，保留兼容接口但不建议新客户端使用。
任务执行器只启动一个实例，重启后将遗留 running 任务标为失败，不自动重试，避免重复变更。

AI 使用三种模式：问答、日志分析、故障诊断。模型没有 SSH 工具或执行权限。
服务端对常见凭据模式做脱敏，但不能覆盖所有秘密格式；上传前仍需人工检查。
输入限 20000 字符，上下文限 20 条/40000 字符，输出限 2000 Token，每用户限 10 次/分钟。
记录模型、输入/输出 Token、成功状态及时间，不保存完整聊天和日志正文。
规则化限流使用开发缓存，单 API 实例适用；扩容前应配置共享缓存。

## Windows 本地

推荐 Python 3.12，项目根目录建立 `.venv`，不覆盖旧虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r opspilot_api/requirements-modern.txt
cd opspilot_api
$env:DJANGO_SETTINGS_MODULE='opspilot_api.settings.local'
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py createsuperuser
..\.venv\Scripts\daphne.exe -b 127.0.0.1 -p 8000 opspilot_api.routing:application
```

在另一终端启动单实例 worker：

```powershell
cd opspilot_api
$env:DJANGO_SETTINGS_MODULE='opspilot_api.settings.local'
..\.venv\Scripts\python.exe manage.py taskworker
```

前端在 `opspilot_web` 运行 `pnpm install --frozen-lockfile`、`pnpm run serve`，访问 http://127.0.0.1:9000。
本地使用现有 `opspilot_api/opspilot_api/db_local.sqlite3`，迁移不会删除已有资产。
开发 SECRET_KEY 未配置时每次启动重新生成，重启后需要重新登录；需稳定登录可在未跟踪的 local_secret.py 设置随机开发密钥。

## 容器启动

Docker Engine 和 Compose 插件运行在宿主机。应用容器使用 Python 3.12 与现有 CI 的依赖范围，前端容器构建后由 Nginx 托管。

1. 将源码部署到独立目录。复制 `.env.example` 为 `.env`，设置随机 Django 密钥与 Fernet 密钥。将文件权限设为 600。
2. 创建 `known_hosts` 文件，写入经可信渠道核对的目标主机公钥。登记资产后使用平台生成的公钥建立授权。
3. 在部署目录运行 `docker compose config --quiet`、`docker compose build api web` 和 `docker compose up -d --no-build`。
4. 执行 `docker compose exec api python manage.py createsuperuser`。可选执行 `docker compose exec api python manage.py seed_examples --owner <管理员用户名>`，填充各模块的隔离样例。
5. 使用 `docker compose ps -a` 核对迁移容器退出码为 0，API 与 Redis 健康，worker、monitor、Prometheus 和 web 正在运行。参阅 [模块示例](MODULE_EXAMPLES.md)。

Compose 的入口默认绑定宿主机回环地址。使用 SSH 本地端口转发访问，或接到已配置 HTTPS 的反向代理。反向代理须支持 WebSocket Upgrade。默认的 SQLite、Prometheus 数据和配置由独立命名卷保存。

在本次 Alibaba Cloud Linux 3 部署中，源码目录为 `/opt/opspilot-ai`，宿主机入口端口为 `19000`。服务器上的 Compose 覆盖配置把 Prometheus 绑定到 Docker 网桥，并采集宿主机已有的回环 Node Exporter。

升级前备份数据卷与部署目录。应用级回退可在部署目录运行 `docker compose stop`，然后用先前已验证的源码或镜像重建应用。保留命名卷以恢复运行。迁移回退时应使用配套数据库备份。

## SSH 凭据与历史泄露

新容器要求 SSH 私钥以 Fernet 加密后保存，密钥仅来自环境变量。
本地兼容已有明文私钥；旧密钥不会自动转换、轮换或下发到远程主机。
发现过代码中的硬编码私钥与固定配置凭据，当前文件已整改。Git 历史未重写。
原凭据是否仍有效未知。发布 GitHub 前必须完成凭据有效性核查、必要的轮换与历史处置，不能仅依靠 .gitignore。
数据库和缓存从 Git 跟踪中移除时保留本地文件，容器构建也排除这些文件。

## 验证

```powershell
cd opspilot_api
$env:DJANGO_SETTINGS_MODULE='opspilot_api.settings.local'
..\.venv\Scripts\python.exe manage.py test host mtask ai
..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

`opspilot_web` 执行 `pnpm run build`。容器构建及真实 SSH、真实 DeepSeek 调用均需独立实测，不以单元测试替代。

## 待继续完善

- 独立凭据绑定与轮换流程，逐步替代全局共享 SSH 密钥。
- 完整终端回放/命令审计；当前只记录会话起止、用户与主机。
- 资产状态目前为人工维护，尚无实时指标采集；资产权限配置当前使用 Django admin。
- AI 结构化结果校验、价格配置和预算硬限制；Token 统计不等于已实现完整计费。
- 基础指标采集与告警，任务取消与调度，生产 MySQL 验证。
- 容器实测与 TLS 部署验收。

### 当前公网入口

- 宿主机 Nginx 在 TCP 19100 提供 HTTPS，并转发到容器原有的回环端口 19000。19000 继续用于 SSH 隧道。
- 公网入口使用专用本地 CA 签发的 IP 证书。CA 签发范围限制为服务公网 IP 和保留测试域，签发私钥未持久化。新设备须经管理员确认后信任 `tls/opspilot-public-ca.pem`，可通过已验证的 SSH 会话获取并核对指纹。服务器证书私钥留在部署目录的 `tls` 子目录。证书到期前需更换。
- Nginx 入口文件为 `/etc/nginx/conf.d/opspilot-public.conf`。撤下公网入口时，将这个文件移出 Nginx 配置目录，再通过 `nginx -t` 和 `systemctl reload nginx` 应用。
