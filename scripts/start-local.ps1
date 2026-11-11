param([ValidateSet('preview','dev')][string]$FrontendMode = 'preview')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if ($FrontendMode -eq 'dev') { $nodePath = (Get-Command node -ErrorAction Stop).Source }
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先建立项目根目录的 .venv 并安装后端依赖' }
foreach ($port in @(8000, 9000)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $port 已占用，请先停止已有开发服务" }
}
$apiDir = Join-Path $projectRoot 'opspilot_api'
$webDir = Join-Path $projectRoot 'opspilot_web'
if ($FrontendMode -eq 'preview' -and !(Test-Path -LiteralPath (Join-Path $webDir 'dist/index.html'))) { throw '请先在 opspilot_web 执行 pnpm run build，或使用 -FrontendMode dev 启动开发模式' }
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
if ($FrontendMode -eq 'dev') {
    $web = Start-Process -FilePath $nodePath -ArgumentList 'node_modules/@vue/cli-service/bin/vue-cli-service.js','serve' -WorkingDirectory $webDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'frontend.stdout.log') -RedirectStandardError (Join-Path $logDir 'frontend.stderr.log') -PassThru
} else {
    $previewScript = Join-Path $projectRoot 'scripts/serve-static.py'
    $distDirectory = Join-Path $webDir 'dist'
    $web = Start-Process -FilePath $pythonPath -ArgumentList ('"{0}"' -f $previewScript),'--directory',('"{0}"' -f $distDirectory) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'frontend.stdout.log') -RedirectStandardError (Join-Path $logDir 'frontend.stderr.log') -PassThru
}
@{ api = $api.Id; worker = $worker.Id; web = $web.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.local-services.json')
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(2)
$ready = $false
try {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $apiResponse = $client.GetAsync('http://127.0.0.1:8000/home/').GetAwaiter().GetResult()
            $webResponse = $client.GetAsync('http://127.0.0.1:9000').GetAwaiter().GetResult()
            $ready = $apiResponse.IsSuccessStatusCode -and $webResponse.IsSuccessStatusCode
            $apiResponse.Dispose()
            $webResponse.Dispose()
            if ($ready) { break }
        } catch { }
        Start-Sleep -Seconds 1
    }
} finally { $client.Dispose() }
if ($ready) {
    Write-Output '服务已就绪：页面 http://127.0.0.1:9000，后端 http://127.0.0.1:8000；日志位于 opspilot_api/logs'
} else {
    Write-Warning '进程已创建，但页面尚未就绪。请检查 opspilot_api/logs 的前后端日志。'
}

if (Test-Path -LiteralPath (Join-Path $projectRoot '.runtime/monitor-test.json')) { & (Join-Path $PSScriptRoot 'start-monitoring.ps1') }
