# 验证与上线

## 本地验证

从仓库根目录运行。后端使用 Python 3.12 和 `opspilot_api/requirements-modern.txt`。前端使用 Node.js 22 与项目锁文件。

```powershell
$env:DJANGO_SETTINGS_MODULE = 'opspilot_api.settings.local'
& .\.venv\Scripts\python.exe opspilot_api\manage.py check
& .\.venv\Scripts\python.exe opspilot_api\manage.py makemigrations --check --dry-run
& .\.venv\Scripts\python.exe opspilot_api\manage.py test host mtask ai users monitoring
Push-Location opspilot_web
pnpm install --frozen-lockfile
pnpm run build
Pop-Location
```

页面验收使用 [模块示例](MODULE_EXAMPLES.md)。在隔离库验证登录、主机列表、权限、诊断证据、待审批、任务结果和报告。用样例账号、保留地址和模拟输出拍截图。

## 上线顺序

1. 核对部署目标、授权范围和版本，记录将要变更的容器。查找在途作业，备份持久化数据并验证备份可读。
2. 依据 [部署说明](DEPLOYMENT.md) 校验 Compose，构建镜像，确认迁移成功和服务健康。保存当前镜像和配置以便回退。
3. 检查入口、证书、登录、API、任务执行器、Prometheus 采样和同步状态。用隔离数据验证审批权限和失败分支。
4. 对 Web SSH、制品发布、服务修复和真实 AI 请求分别记录授权、目标、证据和结果。验证失败时按部署说明回退，并先核对远端真实状态再决定重试。
5. GitHub 上传前核对 Git diff、敏感内容、截图和分支范围。上传后核对分支提交、README 图片链接和实际 CI 状态。

## 已记录的验收

- 本地后端回归曾通过 147 项，前端构建和双管理员 SSH 修复审批页面通过。SSH 修改、密钥重连和回退使用隔离样本与模拟验证。
- Alibaba Cloud Linux 3 容器实例已验证登录、18 个已认证 API、任务执行器心跳及 Node Exporter 实际采样。
- 真实远程 SSH 变更及回退、实际制品发布、AI 模型真实调用和生产规模容量验证，需按场景独立验收。
