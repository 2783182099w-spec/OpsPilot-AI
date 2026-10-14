# 本地测试说明

## 启动与停止

项目根目录执行 `./scripts/start-local.ps1`，后台启动 API、前端与单实例任务执行器。
执行 `./scripts/stop-local.ps1` 停止本脚本记录的服务。进程身份或启动时间不匹配时拒绝停止。
API 使用 Daphne，同时服务 HTTP、WebSocket，开发模式提供 Django admin 静态文件。
入口：http://127.0.0.1:9000；管理员授权入口：http://127.0.0.1:8000/admin/。
重启 API 后若开发密钥没有固定配置，请重新登录。

## 建议手工验证

1. 使用已有账号登录，检查用户名称及菜单。
2. 新建一个测试类别和离线测试主机；本地录入不连接 SSH，也不下发公钥。
3. 搜索、编辑名称和标签、改变人工维护的状态，导出 CSV，确认内容正确。
4. 用另一个普通账号登录，确认看不到未授权资产；管理员通过 Django admin 的 Host.users 授权后再次检查。
5. 对已明确授权且可信指纹已配置的测试主机打开终端，验证断开与重连。
   首次连接先由管理员点击资产行的“登记指纹”，从可信控制台粘贴服务器 `/etc/ssh/ssh_host_ed25519_key.pub` 内容，核对 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256` 输出后确认。
   平台将非标准端口按 `[地址]:端口` 登记到项目 `opspilot_api/known_hosts`，拒绝直接覆盖已登记的不同公钥；这是服务器身份公钥，区别于“同步密钥”下发的用户登录公钥。
6. 提交批量任务：普通用户进入 awaiting_approval；管理员核对命令和主机 ID 后审批，观察运行进度及结果。
7. 日志分析模式粘贴经过人工脱敏的日志，观察摘要、证据和排查建议；调用记录展示 Token 用量。

不得为演示而向生产主机提交命令。任务失败不自动重试；超时只关闭本地 SSH 通道，不能保证远端命令被终止。

## 自动验证

```powershell
cd opspilot_api
$env:DJANGO_SETTINGS_MODULE='opspilot_api.settings.local'
..\.venv\Scripts\python.exe manage.py test host mtask ai
..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

前端在 opspilot_web 执行 `pnpm run build`。
服务启动后，在项目根目录执行 `.\.venv\Scripts\python.exe scripts/smoke-local.py` 验证实际 HTTP 链路。
冒烟脚本只创建并清理临时本地账号/资产，不连接 SSH、不调用付费 AI。

## 本轮实测与限制

已使用独立 Python 3.12 / Django 5.2 / Channels 4 环境验证权限、审批、任务隔离、SSH 指纹策略、私钥加密、AI 脱敏和 WebSocket 拒绝未授权连接。
前端构建通过，资源体积仍有性能警告。
真实 SSH 成功连接、真实 DeepSeek 响应、远端故障排查效果尚未验证。
仍需完善独立主机凭据、终端回放、AI 结构化验证与费用预算、指标采集和告警。
容器与 GitHub 发布不属于当前本地测试阶段。
