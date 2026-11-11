# OpsPilot-AI 项目规则

## 新会话入口

按顺序阅读本文件、[实施计划](docs/IMPLEMENTATION_PLAN.md)、[标准工作流程](docs/SKILLS.md)、[验证与上线](docs/VERIFICATION_AND_RELEASE.md)、[作者与联系](docs/AUTHOR.md)。然后检查当前分支、未提交改动和本次涉及代码。以用户当前请求、仓库和可复核的验收记录为准。

## 项目定位与工作范围

- 面向几十到几百台 Linux 主机的轻量运维平台，主要服务个人和小范围运维团队。Windows 用于本地开发。Alibaba Cloud Linux 3 已有授权部署实例。
- 后端在 opspilot_api/，使用 Django、DRF、Channels、Paramiko。前端在 opspilot_web/，使用 Vue 3 与 Ant Design Vue。本地脚本在 scripts/，项目实施计划在 docs/IMPLEMENTATION_PLAN.md。
- 沿用现有模块和 API。复用 CMDB 主机授权、任务执行器、作业审批、告警与监控能力。依赖或组件引入须对应明确需求，并说明资源与维护成本。

## 先规划再执行

- 开始前检查当前工作区和已有实现。保留用户及其他任务已有改动，避免覆盖未提交文件。
- 小改动简述目标和验证办法。涉及数据迁移、权限、SSH、发布、自愈或跨模块改动时，先列目标、范围、拟改文件、复用点、执行顺序、验证办法及恢复路径。涉及部署或停机的步骤须说明影响。
- 按阶段提交可验证的改动。保留旧数据和已有调用入口；兼容性变化须写清影响。风险或需求变化时更新计划。
- 每次交付区分已实现、本地通过、真实测试环境通过、生产已上线。明确列出实际执行的测试、结果和待验证项。

## 授权、数据和安全

- 默认操作范围是本地。连接远程主机、执行远程命令或改动远端配置须有针对目标和动作的授权。生产部署、改线上数据、开启全局自愈、轮换真实凭据、删除数据、改写 Git 历史、推送 GitHub 或合并主分支需要明确授权。容器化实施在用户确认后单独开展。
- 迁移本地数据库前先备份并校验可读性。重启前核对后台待执行和运行中的任务，避免中断用户作业。使用项目启停脚本操作本项目进程。
- 密钥通过环境变量或忽略的 local_secret.py 管理。提交前检查数据库、日志、私钥、Token、Cookie、真实资产标识和截图，避免把敏感数据写进代码、文档、测试或版本库。截图使用样例数据或打码。
- 身份和主机范围由后端校验。受控发布保留独立审批、主机白名单、制品摘要、首批验证和失败停止。SSH 保留可信 Host Key 校验。结果不确定的远端操作先核查再决定重试或回退。
- 自愈继续区分「仅本次」和「后续同类」。自动模式受总开关、测试环境、允许动作、诊断证据和冷却限制，动作之后复检。AI 助手提供分析建议，服务器执行权限只通过平台已有的授权链路获得。

## 本地验证

以下命令从仓库根目录执行。使用 .venv 和现代依赖集，测试设置与 CI 一致。

    $env:DJANGO_SETTINGS_MODULE = 'opspilot_api.settings.local'
    & .\.venv\Scripts\python.exe opspilot_api\manage.py test host mtask ai users monitoring
    & .\.venv\Scripts\python.exe opspilot_api\manage.py makemigrations --check --dry-run

    Push-Location opspilot_web
    pnpm install --frozen-lockfile
    pnpm run build
    Pop-Location

    .\scripts\start-local.ps1
    .\scripts\stop-local.ps1

- 启动后核对登录页 9000、API 健康入口 8000，以及任务执行器心跳。配置了本地监控时，核对 Prometheus 9090 和同步状态。修改前端后重新构建并复测正常操作入口。
- 权限、任务和迁移相关改动应覆盖越权、失败、中断与重试。测试优先用临时账号和隔离资产。涉及真实 SSH、外部 AI 调用、发布、自愈及负载的验证分别记录环境、范围与结果。
