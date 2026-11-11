$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$runtime = Join-Path $projectRoot '.runtime'
$promRoot = Join-Path $runtime 'prometheus'
$promExe = Join-Path $promRoot 'bin/prometheus.exe'
$recordPath = Join-Path $projectRoot '.monitor-services.json'
if (!(Test-Path -LiteralPath $promExe)) { throw '请先运行 scripts/fetch-monitoring.py 获取并校验 Prometheus' }
if (Test-Path -LiteralPath $recordPath) {
    $old = Get-Content -LiteralPath $recordPath | ConvertFrom-Json
    if (Get-Process -Id $old.prometheus -ErrorAction SilentlyContinue) { Write-Output '监控服务已存在，请先检查或停止'; return }
}
foreach ($port in @(9090,19100)) { if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "监控测试端口 $port 已占用" } }
$logDir = Join-Path $projectRoot 'opspilot_api/logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:DJANGO_SETTINGS_MODULE = 'opspilot_api.settings.local'
Push-Location (Join-Path $projectRoot 'opspilot_api')
try {
    & $pythonPath -c "import django; django.setup(); from monitoring.configuration import defaults,write_config; defaults(); print('Targets/rules:',write_config())"
    if ($LASTEXITCODE -ne 0) { throw '监控配置生成失败' }
} finally { Pop-Location }
& (Join-Path $promRoot 'bin/promtool.exe') check config (Join-Path $promRoot 'prometheus.yml')
if ($LASTEXITCODE -ne 0) { throw 'Prometheus 配置校验失败' }
$env:GOMEMLIMIT = '192MiB'
$env:GOMAXPROCS = '2'
$prom = Start-Process -FilePath $promExe -ArgumentList ('--config.file="{0}"' -f (Join-Path $promRoot 'prometheus.yml')),('--storage.tsdb.path="{0}"' -f (Join-Path $promRoot 'data')),'--web.listen-address=127.0.0.1:9090','--web.enable-lifecycle','--storage.tsdb.retention.time=7d','--storage.tsdb.retention.size=512MB','--query.max-concurrency=4','--query.timeout=8s' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'prometheus.stdout.log') -RedirectStandardError (Join-Path $logDir 'prometheus.stderr.log') -PassThru
$monitor = Start-Process -FilePath $pythonPath -ArgumentList 'manage.py','monitorworker' -WorkingDirectory (Join-Path $projectRoot 'opspilot_api') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'monitor.stdout.log') -RedirectStandardError (Join-Path $logDir 'monitor.stderr.log') -PassThru
$record = @{prometheus=$prom.Id;monitor=$monitor.Id;created_at=[DateTime]::UtcNow.ToString('o')}
$testPath = Join-Path $runtime 'monitor-test.json'
if (Test-Path -LiteralPath $testPath) {
    $test = Get-Content -LiteralPath $testPath | ConvertFrom-Json
    $tunnelScript = Join-Path $projectRoot 'scripts/monitor-tunnel.py'
    $tunnel = Start-Process -FilePath $pythonPath -ArgumentList ('"{0}"' -f $tunnelScript),'--host-id',([string]$test.host_id),'--port',([string]$test.local_port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'tunnel.stdout.log') -RedirectStandardError (Join-Path $logDir 'tunnel.stderr.log') -PassThru
    $record.tunnel = $tunnel.Id
}
$record | ConvertTo-Json | Set-Content -LiteralPath $recordPath
Write-Output '监控服务已启动：Prometheus 仅本机 9090；同步器每 30 秒工作；指标每 60 秒采集'
