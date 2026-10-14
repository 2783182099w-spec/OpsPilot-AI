$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先建立项目根目录的 .venv 并安装后端依赖' }
foreach ($port in @(8000, 9000)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $port 已占用，请先停止已有开发服务" }
}
$apiDir = Join-Path $projectRoot 'opspilot_api'
$webDir = Join-Path $projectRoot 'opspilot_web'
$logDir = Join-Path $apiDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:DJANGO_SETTINGS_MODULE = 'opspilot_api.settings.local'
Push-Location $apiDir
try {
    & $pythonPath manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
} finally { Pop-Location }
$api = Start-Process -FilePath $pythonPath -ArgumentList '-m','daphne','-b','127.0.0.1','-p','8000','opspilot_api.routing:application' -WorkingDirectory $apiDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'server.stdout.log') -RedirectStandardError (Join-Path $logDir 'server.stderr.log') -PassThru
$worker = Start-Process -FilePath $pythonPath -ArgumentList 'manage.py','taskworker' -WorkingDirectory $apiDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'worker.stdout.log') -RedirectStandardError (Join-Path $logDir 'worker.stderr.log') -PassThru
$web = Start-Process -FilePath $nodePath -ArgumentList 'node_modules/@vue/cli-service/bin/vue-cli-service.js','serve' -WorkingDirectory $webDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'frontend.stdout.log') -RedirectStandardError (Join-Path $logDir 'frontend.stderr.log') -PassThru
@{ api = $api.Id; worker = $worker.Id; web = $web.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.local-services.json')
Write-Output '服务已启动：页面 http://127.0.0.1:9000，后端 http://127.0.0.1:8000；日志位于 opspilot_api/logs'
